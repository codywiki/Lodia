from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .domain import ContributionWeight, DataReadinessLevel, RevenueEvent
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
    def __init__(self, data_dir: Optional[str] = None):
        root = Path(data_dir or os.environ.get("LODIA_DATA_DIR", "storage/dev"))
        self.root = root
        self.raw_dir = root / "raw"
        self.export_dir = root / "exports"
        self.db_path = root / "lodia.db"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def submit_text(self, owner_id: str, text: str, allowed_uses: List[str]) -> Dict[str, Any]:
        submission_id = _id("sub")
        raw_hash = _sha256(text)
        raw_path = self.raw_dir / f"{submission_id}.txt"
        raw_path.write_text(text, encoding="utf-8")
        raw_path.chmod(0o600)
        now = _now()
        with self._connect() as conn:
            conn.execute(
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
                    str(raw_path),
                    raw_hash,
                    json.dumps(allowed_uses, ensure_ascii=False),
                    now,
                ),
            )
            self._audit(conn, owner_id, "submission.created", "submission", submission_id, {"source_type": "text"})

        processed = self.process_submission(submission_id)
        return {"submission_id": submission_id, "case": processed}

    def process_submission(self, submission_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            submission = self._get_one(conn, "SELECT * FROM submissions WHERE id = ?", (submission_id,))
            if not submission:
                raise KeyError("submission_not_found")
            raw_text = Path(submission["raw_path"]).read_text(encoding="utf-8")
            known_hashes = [row["canonical_hash"] for row in conn.execute("SELECT canonical_hash FROM cases")]
            allowed_uses = loads(submission["allowed_uses_json"])
            processed = process_text_case(
                raw_text=raw_text,
                owner_id=submission["owner_id"],
                allowed_uses=allowed_uses,
                known_hashes=known_hashes,
            )
            case = to_jsonable(processed.case)
            now = _now()
            conn.execute(
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
            conn.execute("UPDATE submissions SET status = ? WHERE id = ?", (processed.status, submission_id))
            self._audit(conn, case["owner_id"], "case.processed", "case", case["case_id"], {"status": processed.status})
            return self.get_case(case["case_id"], conn=conn)

    def list_cases(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [self._case_from_row(row) for row in conn.execute("SELECT * FROM cases ORDER BY created_at DESC")]

    def get_case(self, case_id: str, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
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
            conn.execute(
                """
                UPDATE cases
                SET status = ?, quality_gate_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, dumps(gate), _now(), case_id),
            )
            conn.execute(
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
    ) -> Dict[str, Any]:
        dataset_id = _id("ds")
        now = _now()
        with self._connect() as conn:
            eligible = [
                self._case_from_row(row)
                for row in conn.execute("SELECT * FROM cases ORDER BY created_at ASC")
                if DRL_ORDER.get(loads(row["quality_gate_json"])["drl"], 0) >= DRL_ORDER[min_drl]
            ]
            if not eligible:
                raise ValueError("no_eligible_cases")

            dataset_dir = self.export_dir / dataset_id
            dataset_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = dataset_dir / "manifest.json"
            report_path = dataset_dir / "quality_report.json"
            data_path = dataset_dir / "data.jsonl"

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
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            report_path.write_text(json.dumps(quality_report, ensure_ascii=False, indent=2), encoding="utf-8")
            data_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")

            conn.execute(
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
                    str(manifest_path),
                    str(report_path),
                    str(data_path),
                    now,
                ),
            )
            for case in eligible:
                conn.execute(
                    "INSERT INTO dataset_cases (dataset_id, case_id) VALUES (?, ?)",
                    (dataset_id, case["case_id"]),
                )

            usage_event_id = _id("use")
            conn.execute(
                """
                INSERT INTO usage_events
                (id, event_type, dataset_id, gross_revenue_cents, direct_cost_cents, billable, payout_eligible, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (usage_event_id, "dataset_exported", dataset_id, gross_revenue_cents, direct_cost_cents, 1, 1, now),
            )
            for case in eligible:
                conn.execute(
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
                conn.execute(
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
            self._audit(conn, "system", "dataset.created", "dataset", dataset_id, {"case_count": len(eligible)})
            dataset = self.get_dataset(dataset_id, conn=conn)
            dataset["payout"] = to_jsonable(payout)
            return dataset

    def get_dataset(self, dataset_id: str, conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
        close = conn is None
        active = conn or self._connect()
        try:
            row = self._get_one(active, "SELECT * FROM datasets WHERE id = ?", (dataset_id,))
            if not row:
                raise KeyError("dataset_not_found")
            case_ids = [item["case_id"] for item in active.execute("SELECT case_id FROM dataset_cases WHERE dataset_id = ?", (dataset_id,))]
            result = dict(row)
            result["case_ids"] = case_ids
            return result
        finally:
            if close:
                active.close()

    def list_usage_events(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM usage_events ORDER BY created_at DESC")]

    def list_payout_events(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM payout_events ORDER BY created_at DESC")]

    def _case_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
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
                """
            )

    def _audit(
        self,
        conn: sqlite3.Connection,
        actor_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: Dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_logs
            (id, actor_id, event_type, entity_type, entity_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_id("audit"), actor_id, event_type, entity_type, entity_id, dumps(payload), _now()),
        )

    @staticmethod
    def _get_one(conn: sqlite3.Connection, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        cursor = conn.execute(query, params)
        return cursor.fetchone()


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
