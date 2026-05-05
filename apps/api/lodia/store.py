from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .config import LodiaSettings
from .database import Database, row_to_dict
from .domain import ContributionWeight, DataReadinessLevel, RevenueEvent
from .identity import hash_password, new_api_token, normalize_email, token_hash, token_suffix, verify_password
from .job_queue import JobQueue, create_job_queue
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
        self.job_queue: JobQueue = create_job_queue(self.settings)
        self._init_db()

    def create_user(
        self,
        email: str,
        password: str,
        roles: List[str],
        display_name: str = "",
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        user_id = _id("usr")
        normalized = normalize_email(email)
        clean_roles = _clean_roles(roles)
        now = _now()
        password_value = hash_password(password, pepper=self.settings.password_pepper)
        with self._session() as conn:
            if self._get_one(conn, "SELECT id FROM users WHERE email = ?", (normalized,)):
                raise ValueError("user_email_exists")
            self._execute(
                conn,
                """
                INSERT INTO users
                (id, email, display_name, password_hash, roles_json, status, created_at, updated_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, normalized, display_name, password_value, dumps(clean_roles), "active", now, now, None),
            )
            self._audit(conn, actor_id, "user.created", "user", user_id, {"email": normalized, "roles": clean_roles})
            return self.get_user(user_id, conn=conn)

    def authenticate_user(self, email: str, password: str, actor_id: str = "login") -> Dict[str, Any]:
        normalized = normalize_email(email)
        with self._session() as conn:
            row = self._get_one(conn, "SELECT * FROM users WHERE email = ?", (normalized,))
            if not row or row["status"] != "active":
                raise ValueError("invalid_credentials")
            if not verify_password(password, row["password_hash"], pepper=self.settings.password_pepper):
                raise ValueError("invalid_credentials")
            self._execute(conn, "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (_now(), _now(), row["id"]))
            self._audit(conn, actor_id, "user.login", "user", row["id"], {})
            token = self.create_api_token(
                user_id=row["id"],
                name="login-session",
                roles=loads(row["roles_json"]),
                actor_id=row["id"],
                conn=conn,
            )
            user = self.get_user(row["id"], conn=conn)
            token["user"] = user
            return token

    def get_user(self, user_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_user(user_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
        if not row:
            raise KeyError("user_not_found")
        return self._user_from_row(row)

    def list_users(self, limit: int = 100, offset: int = 0, status: Optional[str] = None) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        params: List[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._user_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM users
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def create_api_token(
        self,
        user_id: str,
        name: str,
        roles: Optional[List[str]] = None,
        expires_at: Optional[str] = None,
        actor_id: str = "system",
        conn: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.create_api_token(user_id, name, roles, expires_at, actor_id, conn=active)

        user = self.get_user(user_id, conn=conn)
        allowed_roles = set(user["roles"])
        requested_roles = set(_clean_roles(roles or user["roles"]))
        if not requested_roles.issubset(allowed_roles):
            raise ValueError("token_roles_exceed_user_roles")
        expires_at_value = _normalize_expires_at(expires_at)

        token = new_api_token()
        token_id = _id("tok")
        now = _now()
        self._execute(
            conn,
            """
            INSERT INTO api_tokens
            (id, user_id, token_hash, token_suffix, name, roles_json, expires_at, revoked_at, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (token_id, user_id, token_hash(token), token_suffix(token), name, dumps(sorted(requested_roles)), expires_at_value, None, now, None),
        )
        self._audit(conn, actor_id, "api_token.created", "api_token", token_id, {"user_id": user_id, "roles": sorted(requested_roles)})
        return {
            "id": token_id,
            "user_id": user_id,
            "name": name,
            "roles": sorted(requested_roles),
            "token": token,
            "token_suffix": token_suffix(token),
            "expires_at": expires_at_value,
            "created_at": now,
        }

    def revoke_api_token(self, token_id: str, actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
            row = self._get_one(conn, "SELECT * FROM api_tokens WHERE id = ?", (token_id,))
            if not row:
                raise KeyError("token_not_found")
            self._execute(conn, "UPDATE api_tokens SET revoked_at = ? WHERE id = ?", (_now(), token_id))
            self._audit(conn, actor_id, "api_token.revoked", "api_token", token_id, {"user_id": row["user_id"]})
            return self._api_token_from_row(self._get_one(conn, "SELECT * FROM api_tokens WHERE id = ?", (token_id,)))

    def lookup_api_token(self, token: str) -> Optional[Dict[str, Any]]:
        hashed = token_hash(token)
        with self._session() as conn:
            row = self._get_one(
                conn,
                """
                SELECT t.*, u.status AS user_status
                FROM api_tokens t
                JOIN users u ON u.id = t.user_id
                WHERE t.token_hash = ?
                """,
                (hashed,),
            )
            if not row:
                return None
            if row["revoked_at"] or row["user_status"] != "active":
                return None
            if row["expires_at"] and _is_expired(row["expires_at"]):
                return None
            self._execute(conn, "UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (_now(), row["id"]))
            return {
                "subject_id": row["user_id"],
                "roles": loads(row["roles_json"]),
                "token_id": row["id"],
            }

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
        raw_expires_at = _future_hours(self.settings.raw_object_ttl_hours)
        with self._session() as conn:
            self._execute(
                conn,
                """
                INSERT INTO submissions
                (id, owner_id, source_type, status, raw_path, raw_hash, allowed_uses_json,
                 raw_expires_at, raw_deleted_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    owner_id,
                    "text",
                    "quarantined",
                    raw_ref.uri,
                    raw_hash,
                    json.dumps(allowed_uses, ensure_ascii=False),
                    raw_expires_at,
                    None,
                    now,
                ),
            )
            self._audit(conn, actor_id or owner_id, "submission.created", "submission", submission_id, {"source_type": "text"})

            should_enqueue = self.settings.async_processing if enqueue is None else enqueue
            if should_enqueue:
                job_id = self._enqueue_job(
                    conn,
                    job_type="process_submission",
                    payload={"submission_id": submission_id},
                    queue_name="ingestion",
                    actor_id=actor_id or owner_id,
                )
                queued = {"submission_id": submission_id, "status": "queued"}
            else:
                job_id = ""
                queued = {}
        if queued:
            self._publish_job("ingestion", job_id)
            return queued

        processed = self.process_submission(submission_id, actor_id=actor_id or owner_id)
        return {"submission_id": submission_id, "case": processed}

    def process_submission(self, submission_id: str, actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
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
                 drl, quality_score, redaction_json, annotation_json, dedup_json, quality_gate_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case["case_id"],
                    submission_id,
                    case["owner_id"],
                    processed.status,
                    case["redaction"]["redacted_text"],
                    case["dedup"]["raw_hash"],
                    case["dedup"]["canonical_hash"],
                    case["quality_gate"]["drl"],
                    case["annotation"]["quality_score"],
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

    def list_cases(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if status:
            filters.append("status = ?")
            params.append(status)
        if owner_id:
            filters.append("owner_id = ?")
            params.append(owner_id)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._case_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM cases
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def get_case(self, case_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_case(case_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM cases WHERE id = ?", (case_id,))
        if not row:
            raise KeyError("case_not_found")
        return self._case_from_row(row)

    def approve_case(self, case_id: str, reviewer_id: str, notes: str = "") -> Dict[str, Any]:
        with self._session() as conn:
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
                SET status = ?, drl = ?, quality_gate_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, "DRL3", dumps(gate), _now(), case_id),
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

    def reject_case(self, case_id: str, reviewer_id: str, reason: str = "") -> Dict[str, Any]:
        with self._session() as conn:
            self.get_case(case_id, conn=conn)
            self._execute(
                conn,
                """
                UPDATE cases
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                ("rejected", _now(), case_id),
            )
            self._execute(
                conn,
                """
                INSERT INTO reviews (id, case_id, reviewer_id, decision, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (_id("rev"), case_id, reviewer_id, "rejected", reason, _now()),
            )
            self._audit(conn, reviewer_id, "case.review_rejected", "case", case_id, {"reason": reason[:400]})
            return self.get_case(case_id, conn=conn)

    def list_review_queue(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        with self._session() as conn:
            return [
                self._case_from_row(row)
                for row in self._execute(
                    conn,
                    """
                    SELECT * FROM cases
                    WHERE status IN (?, ?)
                    ORDER BY quality_score DESC, created_at ASC, id ASC
                    LIMIT ? OFFSET ?
                    """,
                    ("review_pending", "privacy_review", limit, offset),
                )
            ]

    def create_dataset(
        self,
        name: str,
        purpose: str,
        min_drl: str,
        gross_revenue_cents: int,
        direct_cost_cents: int,
        actor_id: str = "system",
        max_cases: Optional[int] = None,
    ) -> Dict[str, Any]:
        if min_drl not in DRL_ORDER:
            raise ValueError("invalid_min_drl")
        dataset_id = _id("ds")
        now = _now()
        max_cases = _bounded_limit(max_cases or self.settings.dataset_max_cases, self.settings.dataset_max_cases)
        with self._session() as conn:
            eligible = [
                self._case_from_row(row)
                for row in self._execute(
                    conn,
                    """
                    SELECT * FROM cases
                    WHERE drl >= ?
                    ORDER BY quality_score DESC, created_at ASC, id ASC
                    LIMIT ?
                    """,
                    (min_drl, max_cases),
                )
                if DRL_ORDER.get(loads(row["quality_gate_json"])["drl"], 0) >= DRL_ORDER[min_drl]
            ]
            eligible = [case for case in eligible if _case_allowed_for_purpose(case, purpose)]
            if not eligible:
                raise ValueError("no_eligible_cases")

            contract = _data_contract(dataset_id, name, purpose, min_drl, eligible, now)
            contract_violations = _data_contract_violations(contract, eligible)
            if contract_violations:
                raise ValueError(f"data_contract_failed:{','.join(contract_violations)}")

            manifest = {
                "dataset_id": dataset_id,
                "name": name,
                "purpose": purpose,
                "min_drl": min_drl,
                "case_ids": [case["case_id"] for case in eligible],
                "data_contract_id": contract["contract_id"],
                "generated_at": now,
            }
            quality_report = _quality_report(dataset_id, eligible)
            records = [_export_record(case) for case in eligible]
            contract_ref = self.objects.put_text(
                f"exports/{dataset_id}/data_contract.json",
                json.dumps(contract, ensure_ascii=False, indent=2),
            )
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
                (id, name, purpose, min_drl, status, manifest_path, quality_report_path,
                 data_path, data_contract_path, contract_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    contract_ref.uri,
                    "passed",
                    now,
                ),
            )
            self._execute(
                conn,
                """
                INSERT INTO data_contracts
                (id, dataset_id, version, purpose, min_drl, status, contract_json, contract_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract["contract_id"],
                    dataset_id,
                    contract["version"],
                    purpose,
                    min_drl,
                    "passed",
                    dumps(contract),
                    contract_ref.uri,
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
        if conn is None:
            with self._session() as active:
                return self.get_dataset(dataset_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        if not row:
            raise KeyError("dataset_not_found")
        case_ids = [
            item["case_id"]
            for item in self._execute(conn, "SELECT case_id FROM dataset_cases WHERE dataset_id = ?", (dataset_id,))
        ]
        result = row_to_dict(row)
        result["case_ids"] = case_ids
        return result

    def list_usage_events(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        with self._session() as conn:
            return [
                row_to_dict(row)
                for row in self._execute(
                    conn,
                    "SELECT * FROM usage_events ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def list_payout_events(
        self,
        limit: int = 100,
        offset: int = 0,
        contributor_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        params: List[Any] = []
        where = ""
        if contributor_id:
            where = "WHERE contributor_id = ?"
            params.append(contributor_id)
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                row_to_dict(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM payout_events
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def contributor_ledger(self, contributor_id: str) -> Dict[str, Any]:
        payouts = self.list_payout_events(limit=self.settings.max_page_limit, contributor_id=contributor_id)
        pending = sum(item["amount_cents"] for item in payouts if item["status"] == "pending")
        settled = sum(item["amount_cents"] for item in payouts if item["status"] == "settled")
        return {
            "contributor_id": contributor_id,
            "pending_cents": pending,
            "settled_cents": settled,
            "total_cents": pending + settled,
            "payout_count": len(payouts),
            "items": payouts,
        }

    def settle_payout_event(self, payout_id: str, actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
            row = self._get_one(conn, "SELECT * FROM payout_events WHERE id = ?", (payout_id,))
            if not row:
                raise KeyError("payout_not_found")
            self._execute(conn, "UPDATE payout_events SET status = ? WHERE id = ?", ("settled", payout_id))
            self._audit(conn, actor_id, "payout.settled", "payout_event", payout_id, {"contributor_id": row["contributor_id"]})
            return row_to_dict(self._get_one(conn, "SELECT * FROM payout_events WHERE id = ?", (payout_id,)))

    def get_data_contract(self, dataset_id: str) -> Dict[str, Any]:
        with self._session() as conn:
            row = self._get_one(conn, "SELECT * FROM data_contracts WHERE dataset_id = ?", (dataset_id,))
            if not row:
                raise KeyError("data_contract_not_found")
            result = row_to_dict(row)
            result["contract"] = loads(result.pop("contract_json"))
            return result

    def purge_expired_raw_objects(self, limit: int = 100, actor_id: str = "system") -> Dict[str, Any]:
        limit = _bounded_limit(limit, self.settings.max_page_limit)
        purged: List[str] = []
        now = _now()
        with self._session() as conn:
            rows = self._execute(
                conn,
                """
                SELECT id, raw_path
                FROM submissions
                WHERE raw_deleted_at IS NULL AND raw_expires_at IS NOT NULL AND raw_expires_at <= ?
                ORDER BY raw_expires_at ASC
                LIMIT ?
                """,
                (now, limit),
            )
            for row in rows:
                self.objects.delete(row["raw_path"])
                self._execute(conn, "UPDATE submissions SET raw_deleted_at = ? WHERE id = ?", (now, row["id"]))
                self._audit(conn, actor_id, "raw_object.purged", "submission", row["id"], {})
                purged.append(row["id"])
        return {"purged_count": len(purged), "submission_ids": purged}

    def metrics_snapshot(self) -> Dict[str, Any]:
        with self._session() as conn:
            return {
                "cases": _count_by(conn, self, "cases", "status"),
                "jobs": _count_by(conn, self, "jobs", "status"),
                "datasets": _single_count(conn, self, "datasets"),
                "users": _count_by(conn, self, "users", "status"),
                "pending_payout_cents": _sum_where(conn, self, "payout_events", "amount_cents", "status", "pending"),
                "audit_events": _single_count(conn, self, "audit_logs"),
            }

    def create_approval_request(
        self,
        operation_type: str,
        entity_type: str,
        entity_id: str,
        reason: str,
        payload: Dict[str, Any],
        actor_id: str,
    ) -> Dict[str, Any]:
        approval_id = _id("apr")
        now = _now()
        with self._session() as conn:
            self._execute(
                conn,
                """
                INSERT INTO approval_requests
                (id, operation_type, entity_type, entity_id, status, requested_by, decided_by,
                 reason, decision_notes, payload_json, created_at, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (approval_id, operation_type, entity_type, entity_id, "pending", actor_id, None, reason, None, dumps(payload), now, None),
            )
            self._audit(conn, actor_id, "approval.requested", "approval_request", approval_id, {"operation_type": operation_type})
            return self._approval_from_row(self._get_one(conn, "SELECT * FROM approval_requests WHERE id = ?", (approval_id,)))

    def decide_approval_request(self, approval_id: str, decision: str, notes: str, actor_id: str) -> Dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("invalid_approval_decision")
        with self._session() as conn:
            row = self._get_one(conn, "SELECT * FROM approval_requests WHERE id = ?", (approval_id,))
            if not row:
                raise KeyError("approval_not_found")
            if row["status"] != "pending":
                raise ValueError("approval_already_decided")
            self._execute(
                conn,
                """
                UPDATE approval_requests
                SET status = ?, decided_by = ?, decision_notes = ?, decided_at = ?
                WHERE id = ?
                """,
                (decision, actor_id, notes, _now(), approval_id),
            )
            self._audit(conn, actor_id, f"approval.{decision}", "approval_request", approval_id, {})
            return self._approval_from_row(self._get_one(conn, "SELECT * FROM approval_requests WHERE id = ?", (approval_id,)))

    def list_approval_requests(self, limit: int = 100, offset: int = 0, status: Optional[str] = None) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        params: List[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._approval_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM approval_requests
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def list_audit_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        entity_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if entity_id:
            filters.append("entity_id = ?")
            params.append(entity_id)
        if event_type:
            filters.append("event_type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            rows = self._execute(
                conn,
                f"""
                SELECT * FROM audit_logs
                {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            )
            return [self._audit_from_row(row) for row in rows]

    def enqueue_job(self, job_type: str, payload: Dict[str, Any], queue_name: str = "default", actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
            job_id = self._enqueue_job(conn, job_type, payload, queue_name, actor_id)
            job = self.get_job(job_id, conn=conn)
        self._publish_job(queue_name, job_id)
        return job

    def claim_next_job(self, queue_name: str = "default", worker_id: str = "worker") -> Optional[Dict[str, Any]]:
        queued_job_id = self.job_queue.pop(queue_name, timeout_seconds=0)
        if queued_job_id:
            claimed = self.claim_job_by_id(queued_job_id, worker_id=worker_id)
            if claimed:
                return claimed

        with self._session() as conn:
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
            now = _now()
            cursor = self._execute(
                conn,
                """
                UPDATE jobs
                SET status = ?, locked_at = ?, locked_by = ?, attempts = attempts + 1, updated_at = ?
                WHERE id = ? AND status = ? AND available_at <= ?
                """,
                ("running", now, worker_id, now, job_id, "queued", now),
            )
            if cursor.rowcount == 0:
                return None
            self._audit(conn, worker_id, "job.claimed", "job", job_id, {"queue_name": queue_name})
            return self.get_job(job_id, conn=conn)

    def claim_job_by_id(self, job_id: str, worker_id: str = "worker") -> Optional[Dict[str, Any]]:
        with self._session() as conn:
            now = _now()
            if self.db.use_postgres:
                row = self._get_one(
                    conn,
                    """
                    UPDATE jobs
                    SET status = ?, locked_at = ?, locked_by = ?, attempts = attempts + 1, updated_at = ?
                    WHERE id = ? AND status = ? AND available_at <= ?
                    RETURNING *
                    """,
                    ("running", now, worker_id, now, job_id, "queued", now),
                )
                if not row:
                    return None
                job = self._job_from_row(row)
                self._audit(conn, worker_id, "job.claimed", "job", job_id, {"queue_name": job["queue_name"]})
                return job

            cursor = self._execute(
                conn,
                """
                UPDATE jobs
                SET status = ?, locked_at = ?, locked_by = ?, attempts = attempts + 1, updated_at = ?
                WHERE id = ? AND status = ? AND available_at <= ?
                """,
                ("running", now, worker_id, now, job_id, "queued", now),
            )
            if cursor.rowcount == 0:
                return None
            job = self.get_job(job_id, conn=conn)
            self._audit(conn, worker_id, "job.claimed", "job", job_id, {"queue_name": job["queue_name"]})
            return job

    def complete_job(self, job_id: str, worker_id: str = "worker") -> None:
        with self._session() as conn:
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
        republish: Optional[tuple[str, str]] = None
        with self._session() as conn:
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
            if next_status == "queued":
                republish = (job["queue_name"], job_id)
        if republish:
            self._publish_job(republish[0], republish[1])

    def get_job(self, job_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_job(job_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not row:
            raise KeyError("job_not_found")
        return self._job_from_row(row)

    def list_jobs(self, limit: int = 100, offset: int = 0, status: Optional[str] = None) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        params: List[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._job_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM jobs
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
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

    def _publish_job(self, queue_name: str, job_id: str) -> None:
        try:
            self.job_queue.publish(queue_name, job_id)
        except Exception:
            # The database record remains the source of truth. If Redis is down,
            # DB polling workers can still drain queued jobs and readiness will
            # surface the queue health failure.
            return None

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

    def _user_from_row(self, row: Any) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "roles": loads(row["roles_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login_at": row["last_login_at"],
        }

    def _api_token_from_row(self, row: Any) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "name": row["name"],
            "roles": loads(row["roles_json"]),
            "token_suffix": row["token_suffix"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"],
        }

    def _job_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result["payload"] = loads(result.pop("payload_json"))
        return result

    def _audit_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result["payload"] = loads(result.pop("payload_json"))
        return result

    def _approval_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result["payload"] = loads(result.pop("payload_json"))
        return result

    @contextmanager
    def _session(self):
        with self.db.session() as conn:
            yield conn

    def _execute(self, conn: Any, query: str, params: tuple = ()):
        return self.db.execute(conn, query, params)

    def _init_db(self) -> None:
        with self._session() as conn:
            self.db.execute_script(conn, SCHEMA_SQL)
            self._apply_versioned_migrations(conn)

    def readiness_check(self) -> Dict[str, Any]:
        checks: Dict[str, Any] = {}
        try:
            self.db.ping()
            checks["database"] = {"ok": True, "backend": "postgres" if self.settings.use_postgres else "sqlite"}
        except Exception as exc:
            checks["database"] = {"ok": False, "error": type(exc).__name__}

        try:
            checks["object_storage"] = self.objects.health_check()
        except Exception as exc:
            checks["object_storage"] = {"ok": False, "backend": self.settings.object_storage_backend, "error": type(exc).__name__}

        try:
            checks["queue"] = self.job_queue.health_check()
        except Exception as exc:
            checks["queue"] = {"ok": False, "backend": self.settings.queue_backend, "error": type(exc).__name__}

        checks["ok"] = all(item.get("ok") for key, item in checks.items() if key != "ok")
        return checks

    def _apply_versioned_migrations(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """,
        )
        self._ensure_submission_columns(conn)
        self._ensure_dataset_columns(conn)
        self._ensure_case_query_columns(conn)
        self._execute(
            conn,
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            ("20260506_p0_foundation", _now()),
        )

    def _ensure_submission_columns(self, conn: Any) -> None:
        columns = self.db.column_names(conn, "submissions")
        if "raw_expires_at" not in columns:
            self._execute(conn, "ALTER TABLE submissions ADD COLUMN raw_expires_at TEXT")
        if "raw_deleted_at" not in columns:
            self._execute(conn, "ALTER TABLE submissions ADD COLUMN raw_deleted_at TEXT")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_submissions_raw_expiry ON submissions(raw_expires_at, raw_deleted_at)")

    def _ensure_dataset_columns(self, conn: Any) -> None:
        columns = self.db.column_names(conn, "datasets")
        if "data_contract_path" not in columns:
            self._execute(conn, "ALTER TABLE datasets ADD COLUMN data_contract_path TEXT")
        if "contract_status" not in columns:
            self._execute(conn, "ALTER TABLE datasets ADD COLUMN contract_status TEXT")

    def _ensure_case_query_columns(self, conn: Any) -> None:
        columns = self.db.column_names(conn, "cases")
        if "drl" not in columns:
            self._execute(conn, "ALTER TABLE cases ADD COLUMN drl TEXT")
        if "quality_score" not in columns:
            self._execute(conn, "ALTER TABLE cases ADD COLUMN quality_score REAL")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_cases_drl_quality ON cases(drl, quality_score)")

        rows = self._execute(
            conn,
            """
            SELECT id, annotation_json, quality_gate_json
            FROM cases
            WHERE drl IS NULL OR quality_score IS NULL
            """,
        )
        for row in rows:
            annotation = loads(row["annotation_json"])
            gate = loads(row["quality_gate_json"])
            self._execute(
                conn,
                "UPDATE cases SET drl = ?, quality_score = ? WHERE id = ?",
                (gate.get("drl", "DRL0"), float(annotation.get("quality_score", 0.0)), row["id"]),
            )

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
    raw_expires_at TEXT,
    raw_deleted_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_raw_expiry ON submissions(raw_expires_at, raw_deleted_at);

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    redacted_text TEXT NOT NULL,
    raw_hash TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    drl TEXT,
    quality_score REAL,
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
CREATE INDEX IF NOT EXISTS idx_cases_status_created ON cases(status, created_at);
CREATE INDEX IF NOT EXISTS idx_cases_owner_created ON cases(owner_id, created_at);
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
    data_contract_path TEXT,
    contract_status TEXT,
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
CREATE INDEX IF NOT EXISTS idx_usage_events_created ON usage_events(created_at);

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
CREATE INDEX IF NOT EXISTS idx_payout_events_contributor_created ON payout_events(contributor_id, created_at);
CREATE INDEX IF NOT EXISTS idx_payout_events_usage ON payout_events(usage_event_id);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_status_created ON users(status, created_at);

CREATE TABLE IF NOT EXISTS api_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    token_suffix TEXT NOT NULL,
    name TEXT NOT NULL,
    roles_json TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_revoked ON api_tokens(revoked_at);

CREATE TABLE IF NOT EXISTS data_contracts (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL UNIQUE,
    version TEXT NOT NULL,
    purpose TEXT NOT NULL,
    min_drl TEXT NOT NULL,
    status TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    contract_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (dataset_id) REFERENCES datasets(id)
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    decided_by TEXT,
    reason TEXT NOT NULL,
    decision_notes TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_status ON approval_requests(status, created_at);

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
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_created ON audit_logs(event_type, created_at);

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
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
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


def _data_contract(dataset_id: str, name: str, purpose: str, min_drl: str, cases: List[Dict[str, Any]], now: str) -> Dict[str, Any]:
    return {
        "contract_id": _id("dc"),
        "version": "2026-05-06.p0",
        "dataset_id": dataset_id,
        "name": name,
        "purpose": purpose,
        "min_drl": min_drl,
        "case_count": len(cases),
        "case_ids": [case["case_id"] for case in cases],
        "rules": {
            "contains_raw_data": False,
            "requires_redaction_passed": True,
            "requires_purpose_authorized": True,
            "requires_no_required_actions": True,
            "requires_human_review_for_drl3_plus": True,
        },
        "generated_at": now,
    }


def _data_contract_violations(contract: Dict[str, Any], cases: List[Dict[str, Any]]) -> List[str]:
    violations: List[str] = []
    min_drl = contract["min_drl"]
    purpose = contract["purpose"]
    for case in cases:
        if not case["redaction"]["passed"]:
            violations.append("redaction_not_passed")
        if DRL_ORDER.get(case["quality_gate"]["drl"], 0) < DRL_ORDER[min_drl]:
            violations.append("drl_below_minimum")
        if not _case_allowed_for_purpose(case, purpose):
            violations.append("purpose_not_authorized")
        if case["quality_gate"].get("required_actions"):
            violations.append("required_actions_open")
    return sorted(set(violations))


def _export_record(case: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "owner_id": case["owner_id"],
        "drl": case["quality_gate"]["drl"],
        "redacted_turns": [{"role": "mixed", "content": case["redacted_text"]}],
        "annotation": case["annotation"],
        "license": {"allowed_uses": case["quality_gate"]["allowed_uses"]},
    }


def _case_allowed_for_purpose(case: Dict[str, Any], purpose: str) -> bool:
    allowed_uses = set(case["quality_gate"].get("allowed_uses", []))
    return purpose in allowed_uses or (purpose == "commercial_dataset" and "training" in allowed_uses)


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


def _count_by(conn: Any, store: LodiaStore, table_name: str, column_name: str) -> Dict[str, int]:
    rows = store._execute(conn, f"SELECT {column_name} AS key, COUNT(*) AS value FROM {table_name} GROUP BY {column_name}")
    return {row["key"]: row["value"] for row in rows}


def _single_count(conn: Any, store: LodiaStore, table_name: str) -> int:
    row = store._get_one(conn, f"SELECT COUNT(*) AS value FROM {table_name}")
    return int(row["value"]) if row else 0


def _sum_where(conn: Any, store: LodiaStore, table_name: str, amount_column: str, where_column: str, where_value: str) -> int:
    row = store._get_one(
        conn,
        f"SELECT COALESCE(SUM({amount_column}), 0) AS value FROM {table_name} WHERE {where_column} = ?",
        (where_value,),
    )
    return int(row["value"]) if row else 0


def _page_bounds(limit: int, offset: int, max_limit: int) -> tuple[int, int]:
    return _bounded_limit(limit, max_limit), max(0, offset)


def _bounded_limit(limit: int, max_limit: int) -> int:
    return max(1, min(limit, max_limit))


def _clean_roles(roles: List[str]) -> List[str]:
    allowed = {"admin", "reviewer", "contributor"}
    clean = sorted({role for role in roles if role in allowed})
    if not clean:
        raise ValueError("roles_required")
    return clean


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_expires_at(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid_expires_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _is_expired(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc)


def _future_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=max(1, hours))).isoformat()
