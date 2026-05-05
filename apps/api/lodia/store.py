from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import LodiaSettings
from .database import Database, row_to_dict
from .domain import ContributionWeight, DataReadinessLevel, RevenueEvent
from .object_storage import ObjectStorage, create_object_storage
from .payout import calculate_payout
from .pipeline import process_text_case
from .serde import dumps, loads, to_jsonable


DRL_ORDER = {
    "DRL0": 0,
    "DRL1": 1,
    "DRL2": 2,
    "DRL3": 3,
    "DRL4": 4,
    "DRL5": 5,
}


class LodiaStore:
    def __init__(
        self,
        data_dir: Optional[str] = None,
        settings: Optional[LodiaSettings] = None,
        object_storage: Optional[ObjectStorage] = None,
    ):
        self.settings = settings or LodiaSettings.from_env(data_dir=data_dir)
        self.db = Database(self.settings)
        self.objects = object_storage or create_object_storage(self.settings)
        self._init_db()

    def submit_text(
        self,
        owner_id: str,
        text: str,
        allowed_uses: List[str],
        actor_id: Optional[str] = None,
        enqueue: Optional[bool] = None,
    ) -> Dict[str, Any]:
        submission_id = _id("sub")
        raw_hash = _sha256(text)
        raw_ref = self.objects.put_text(f"raw/{submission_id}.txt", text)
        now = _now()
        with self._connect() as conn:
            self._execute(
                conn,
                """
                INSERT INTO submissions
                (id, owner_id, source_type, status, raw_path, raw_hash, allowed_uses_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    owner_id,
                    "text",
                    "quarantined",
                    raw_ref.uri,
                    raw_hash,
                    json.dumps(allowed_uses, ensure_ascii=False),
                    now,
                ),
            )
            self._audit(conn, actor_id or owner_id, "submission.created", "submission", submission_id, {"source_type": "text"})

            should_enqueue = self.settings.async_processing if enqueue is None else enqueue
            if should_enqueue:
                self._enqueue_job(
                    conn,
                    job_type="process_submission",
                    payload={"submission_id": submission_id},
                    queue_name="ingestion",
                    actor_id=actor_id or owner_id,
                )
                return {"submission_id": submission_id, "status": "queued"}

        processed = self.process_submission(submission_id, actor_id=actor_id or owner_id)
        return {"submission_id": submission_id, "case": processed}

    def process_submission(self, submission_id: str, actor_id: str = "system") -> Dict[str, Any]:
        with self._connect() as conn:
            submission = self._get_one(conn, "SELECT * FROM submissions WHERE id = ?", (submission_id,))
            if not submission:
                raise KeyError("submission_not_found")
            existing_case = self._get_one(conn, "SELECT * FROM cases WHERE submission_id = ?", (submission_id,))
            if existing_case:
                self._audit(conn, actor_id, "case.process_skipped", "case", existing_case["id"], {"reason": "already_processed"})
                return self._case_from_row(existing_case)
            raw_text = self.objects.read_text(submission["raw_path"])
            known_hashes = [row["canonical_hash"] for row in self._execute(conn, "SELECT canonical_hash FROM cases")]
            allowed_uses = loads(submission["allowed_uses_json"])
            processed = process_text_case(
                raw_text=raw_text,
                owner_id=submission["owner_id"],
                allowed_uses=allowed_uses,
                known_hashes=known_hashes,
            )
            case = to_jsonable(processed.case)
            now = _now()
            self._execute(
                conn,
                """
                INSERT INTO cases
                (id, submission_id, owner_id, status, redacted_text, raw_hash, canonical_hash,
                 redaction_json, annotation_json, dedup_json, quality_gate_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case["case_id"],
                    submission_id,
                    case["owner_id"],
                    processed.status,
                    case["redaction"]["redacted_text"],
                    case["dedup"]["raw_hash"],
                    case["dedup"]["canonical_hash"],
                    dumps(case["redaction"]),
                    dumps(case["annotation"]),
                    dumps(case["dedup"]),
                    dumps(case["quality_gate"]),
                    now,
                    now,
                ),
            )
            self._execute(conn, "UPDATE submissions SET status = ? WHERE id = ?", (processed.status, submission_id))
            self._audit(conn, actor_id, "case.processed", "case", case["case_id"], {"status": processed.status})
            return self.get_case(case["case_id"], conn=conn)

    def list_cases(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [
                self._case_from_row(row)
                for row in self._execute(conn, "SELECT * FROM cases ORDER BY created_at DESC")
            ]

    def get_case(self, case_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        close = conn is None
        active = conn or self._connect()
        try:
            row = self._get_one(active, "SELECT * FROM cases WHERE id = ?", (case_id,))
            if not row:
                raise KeyError("case_not_found")
            return self._case_from_row(row)
        finally:
            if close:
                active.close()

    def approve_case(self, case_id: str, reviewer_id: str, notes: str = "") -> Dict[str, Any]:
        with self._connect() as conn:
            case = self.get_case(case_id, conn=conn)
            gate = dict(case["quality_gate"])
            if not any(use in case["quality_gate"]["allowed_uses"] for use in ["commercial_dataset", "training", "gold_eval"]):
                raise ValueError("commercial_use_not_authorized")
            if not case["redaction"]["passed"]:
                raise ValueError("privacy_gate_not_passed")
            gate["drl"] = "DRL3"
            gate["required_actions"] = [action for action in gate.get("required_actions", []) if action != "human_review"]
            gate["commercial_ready"] = True
            status = "commercial_ready"
            self._execute(
                conn,
                """
                UPDATE cases
                SET status = ?, quality_gate_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, dumps(gate), _now(), case_id),
            )
            self._execute(
                conn,
                """
                INSERT INTO reviews (id, case_id, reviewer_id, decision, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (_id("rev"), case_id, reviewer_id, "approved", notes, _now()),
            )
            self._audit(conn, reviewer_id, "case.review_approved", "case", case_id, {"drl": "DRL3"})
            return self.get_case(case_id, conn=conn)

    def create_dataset(
        self,
        name: str,
        purpose: str,
        min_drl: str,
        gross_revenue_cents: int,
        direct_cost_cents: int,
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        dataset_id = _id("ds")
        now = _now()
        with self._connect() as conn:
            eligible = [
                self._case_from_row(row)
                for row in self._execute(conn, "SELECT * FROM cases ORDER BY created_at ASC")
                if DRL_ORDER.get(loads(row["quality_gate_json"])["drl"], 0) >= DRL_ORDER[min_drl]
            ]
            if not eligible:
                raise ValueError("no_eligible_cases")

            manifest = {
                "dataset_id": dataset_id,
                "name": name,
                "purpose": purpose,
                "min_drl": min_drl,
                "case_ids": [case["case_id"] for case in eligible],
                "generated_at": now,
            }
            quality_report = _quality_report(dataset_id, eligible)
            records = [_export_record(case) for case in eligible]
            manifest_ref = self.objects.put_text(
                f"exports/{dataset_id}/manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            report_ref = self.objects.put_text(
                f"exports/{dataset_id}/quality_report.json",
                json.dumps(quality_report, ensure_ascii=False, indent=2),
            )
            data_ref = self.objects.put_text(
                f"exports/{dataset_id}/data.jsonl",
                "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
            )

            self._execute(
                conn,
                """
                INSERT INTO datasets
                (id, name, purpose, min_drl, status, manifest_path, quality_report_path, data_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    name,
                    purpose,
                    min_drl,
                    "ready",
                    manifest_ref.uri,
                    report_ref.uri,
                    data_ref.uri,
                    now,
                ),
            )
            for case in eligible:
                self._execute(
                    conn,
                    "INSERT INTO dataset_cases (dataset_id, case_id) VALUES (?, ?)",
                    (dataset_id, case["case_id"]),
                )

            usage_event_id = _id("use")
            self._execute(
                conn,
                """
                INSERT INTO usage_events
                (id, event_type, dataset_id, gross_revenue_cents, direct_cost_cents, billable, payout_eligible, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (usage_event_id, "dataset_exported", dataset_id, gross_revenue_cents, direct_cost_cents, 1, 1, now),
            )
            for case in eligible:
                self._execute(
                    conn,
                    """
                    INSERT INTO usage_event_cases (usage_event_id, case_id)
                    VALUES (?, ?)
                    """,
                    (usage_event_id, case["case_id"]),
                )

            payout = calculate_payout(
                RevenueEvent(
                    event_id=usage_event_id,
                    gross_revenue_cents=gross_revenue_cents,
                    direct_cost_cents=direct_cost_cents,
                ),
                [_contribution_from_case(case) for case in eligible],
            )
            for allocation in payout.allocations:
                self._execute(
                    conn,
                    """
                    INSERT INTO payout_events
                    (id, usage_event_id, contributor_id, case_id, amount_cents, weight, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _id("pay"),
                        usage_event_id,
                        allocation.contributor_id,
                        allocation.case_id,
                        allocation.amount_cents,
                        allocation.weight,
                        allocation.status,
                        now,
                    ),
                )
            self._audit(conn, actor_id, "dataset.created", "dataset", dataset_id, {"case_count": len(eligible)})
            dataset = self.get_dataset(dataset_id, conn=conn)
            dataset["payout"] = to_jsonable(payout)
            return dataset

    def get_dataset(self, dataset_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        close = conn is None
        active = conn or self._connect()
        try:
            row = self._get_one(active, "SELECT * FROM datasets WHERE id = ?", (dataset_id,))
            if not row:
                raise KeyError("dataset_not_found")
            case_ids = [
                item["case_id"]
                for item in self._execute(active, "SELECT case_id FROM dataset_cases WHERE dataset_id = ?", (dataset_id,))
            ]
            result = row_to_dict(row)
            result["case_ids"] = case_ids
            return result
        finally:
            if close:
                active.close()

    def list_usage_events(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [row_to_dict(row) for row in self._execute(conn, "SELECT * FROM usage_events ORDER BY created_at DESC")]

    def list_payout_events(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [row_to_dict(row) for row in self._execute(conn, "SELECT * FROM payout_events ORDER BY created_at DESC")]

    def list_audit_logs(self, limit: int = 100, entity_id: Optional[str] = None) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._connect() as conn:
            if entity_id:
                rows = self._execute(
                    conn,
                    """
                    SELECT * FROM audit_logs
                    WHERE entity_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (entity_id, limit),
                )
            else:
                rows = self._execute(
                    conn,
                    "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            return [self._audit_from_row(row) for row in rows]

    def enqueue_job(self, job_type: str, payload: Dict[str, Any], queue_name: str = "default", actor_id: str = "system") -> Dict[str, Any]:
        with self._connect() as conn:
            job_id = self._enqueue_job(conn, job_type, payload, queue_name, actor_id)
            return self.get_job(job_id, conn=conn)

    def claim_next_job(self, queue_name: str = "default", worker_id: str = "worker") -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            if self.db.use_postgres:
                row = self._get_one(
                    conn,
                    """
                    UPDATE jobs
                    SET status = ?, locked_at = ?, locked_by = ?, attempts = attempts + 1, updated_at = ?
                    WHERE id = (
                        SELECT id FROM jobs
                        WHERE queue_name = ? AND status = ? AND available_at <= ?
                        ORDER BY created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *
                    """,
                    ("running", _now(), worker_id, _now(), queue_name, "queued", _now()),
                )
                if not row:
                    return None
                job = self._job_from_row(row)
                self._audit(conn, worker_id, "job.claimed", "job", job["id"], {"queue_name": queue_name})
                return job

            row = self._get_one(
                conn,
                """
                SELECT * FROM jobs
                WHERE queue_name = ? AND status = ? AND available_at <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (queue_name, "queued", _now()),
            )
            if not row:
                return None
            job_id = row["id"]
            self._execute(
                conn,
                """
                UPDATE jobs
                SET status = ?, locked_at = ?, locked_by = ?, attempts = attempts + 1, updated_at = ?
                WHERE id = ?
                """,
                ("running", _now(), worker_id, _now(), job_id),
            )
            self._audit(conn, worker_id, "job.claimed", "job", job_id, {"queue_name": queue_name})
            return self.get_job(job_id, conn=conn)

    def complete_job(self, job_id: str, worker_id: str = "worker") -> None:
        with self._connect() as conn:
            self._execute(
                conn,
                """
                UPDATE jobs
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                ("completed", _now(), job_id),
            )
            self._audit(conn, worker_id, "job.completed", "job", job_id, {})

    def fail_job(self, job_id: str, error: str, worker_id: str = "worker") -> None:
        with self._connect() as conn:
            job = self.get_job(job_id, conn=conn)
            next_status = "failed" if job["attempts"] >= job["max_attempts"] else "queued"
            self._execute(
                conn,
                """
                UPDATE jobs
                SET status = ?, error = ?, locked_at = NULL, locked_by = NULL, updated_at = ?
                WHERE id = ?
                """,
                (next_status, error[:2000], _now(), job_id),
            )
            self._audit(conn, worker_id, "job.failed", "job", job_id, {"status": next_status})

    def get_job(self, job_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        close = conn is None
        active = conn or self._connect()
        try:
            row = self._get_one(active, "SELECT * FROM jobs WHERE id = ?", (job_id,))
            if not row:
                raise KeyError("job_not_found")
            return self._job_from_row(row)
        finally:
            if close:
                active.close()

    def list_jobs(self, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._connect() as conn:
            return [
                self._job_from_row(row)
                for row in self._execute(conn, "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
            ]

    def _enqueue_job(
        self,
        conn: Any,
        job_type: str,
        payload: Dict[str, Any],
        queue_name: str,
        actor_id: str,
    ) -> str:
        job_id = _id("job")
        now = _now()
        self._execute(
            conn,
            """
            INSERT INTO jobs
            (id, queue_name, job_type, status, payload_json, attempts, max_attempts, error,
             available_at, locked_at, locked_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, queue_name, job_type, "queued", dumps(payload), 0, 3, "", now, None, None, now, now),
        )
        self._audit(conn, actor_id, "job.enqueued", "job", job_id, {"job_type": job_type})
        return job_id

    def _case_from_row(self, row: Any) -> Dict[str, Any]:
        return {
            "case_id": row["id"],
            "submission_id": row["submission_id"],
            "owner_id": row["owner_id"],
            "status": row["status"],
            "redacted_text": row["redacted_text"],
            "raw_hash": row["raw_hash"],
            "canonical_hash": row["canonical_hash"],
            "redaction": loads(row["redaction_json"]),
            "annotation": loads(row["annotation_json"]),
            "dedup": loads(row["dedup_json"]),
            "quality_gate": loads(row["quality_gate_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _job_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result["payload"] = loads(result.pop("payload_json"))
        return result

    def _audit_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result["payload"] = loads(result.pop("payload_json"))
        return result

    def _connect(self):
        return self.db.connect()

    def _execute(self, conn: Any, query: str, params: tuple = ()):
        return self.db.execute(conn, query, params)

    def _init_db(self) -> None:
        with self._connect() as conn:
            self.db.execute_script(conn, SCHEMA_SQL)

    def _audit(
        self,
        conn: Any,
        actor_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: Dict[str, Any],
    ) -> None:
        self._execute(
            conn,
            """
            INSERT INTO audit_logs
            (id, actor_id, event_type, entity_type, entity_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_id("audit"), actor_id, event_type, entity_type, entity_id, dumps(payload), _now()),
        )

    def _get_one(self, conn: Any, query: str, params: tuple = ()) -> Optional[Any]:
        return self.db.fetch_one(conn, query, params)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    allowed_uses_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    redacted_text TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    redaction_json TEXT NOT NULL,
    annotation_json TEXT NOT NULL,
    dedup_json TEXT NOT NULL,
    quality_gate_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (submission_id) REFERENCES submissions(id)
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner_id);
CREATE INDEX IF NOT EXISTS idx_cases_canonical_hash ON cases(canonical_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_submission ON cases(submission_id);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    min_drl TEXT NOT NULL,
    status TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    quality_report_path TEXT NOT NULL,
    data_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_cases (
    dataset_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    PRIMARY KEY (dataset_id, case_id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(id),
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    dataset_id TEXT,
    gross_revenue_cents INTEGER NOT NULL,
    direct_cost_cents INTEGER NOT NULL,
    billable INTEGER NOT NULL,
    payout_eligible INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_event_cases (
    usage_event_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    PRIMARY KEY (usage_event_id, case_id),
    FOREIGN KEY (usage_event_id) REFERENCES usage_events(id),
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

CREATE TABLE IF NOT EXISTS payout_events (
    id TEXT PRIMARY KEY,
    usage_event_id TEXT NOT NULL,
    contributor_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    weight REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (usage_event_id) REFERENCES usage_events(id),
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    queue_name TEXT NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    error TEXT NOT NULL,
    available_at TEXT NOT NULL,
    locked_at TEXT,
    locked_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_queue_status ON jobs(queue_name, status, available_at);
"""


def _quality_report(dataset_id: str, cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    drl_distribution: Dict[str, int] = {}
    total_quality = 0.0
    for case in cases:
        drl = case["quality_gate"]["drl"]
        drl_distribution[drl] = drl_distribution.get(drl, 0) + 1
        total_quality += case["annotation"]["quality_score"]
    return {
        "dataset_id": dataset_id,
        "case_count": len(cases),
        "average_quality_score": round(total_quality / len(cases), 4),
        "drl_distribution": drl_distribution,
        "contains_raw_data": False,
        "human_review_required_for_drl3_plus": True,
    }


def _export_record(case: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "owner_id": case["owner_id"],
        "drl": case["quality_gate"]["drl"],
        "redacted_turns": [{"role": "mixed", "content": case["redacted_text"]}],
        "annotation": case["annotation"],
        "license": {"allowed_uses": case["quality_gate"]["allowed_uses"]},
    }


def _contribution_from_case(case: Dict[str, Any]) -> ContributionWeight:
    duplicate_penalty = 0.2 if case["dedup"]["duplicate_status"] != "unique" else 1.0
    license_weight = 1.2 if "training" in case["quality_gate"]["allowed_uses"] else 1.0
    return ContributionWeight(
        case_id=case["case_id"],
        contributor_id=case["owner_id"],
        quality_score=case["annotation"]["quality_score"],
        novelty_score=case["dedup"]["novelty_score"],
        source_trust_score=1.0,
        license_weight=license_weight,
        usage_count=1,
        duplicate_penalty=duplicate_penalty,
        reviewed_level=DataReadinessLevel(case["quality_gate"]["drl"]),
    )


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
