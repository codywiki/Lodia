from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .assets import inspect_asset, sanitize_filename
from .config import LodiaSettings
from .database import Database, row_to_dict
from .domain import ContributionWeight, DataReadinessLevel, RevenueEvent
from .identity import hash_password, new_api_token, normalize_email, token_hash, token_suffix, verify_password
from .job_queue import JobQueue, create_job_queue
from .model_gateway import annotation_invocation, extract_asset_text, extraction_invocation
from .object_storage import ObjectStorage, create_object_storage
from .payout import calculate_payout
from .pipeline import process_text_case
from .redaction import redact_text
from .serde import dumps, loads, to_jsonable


DRL_ORDER = {
    "DRL0": 0,
    "DRL1": 1,
    "DRL2": 2,
    "DRL3": 3,
    "DRL4": 4,
    "DRL5": 5,
}

DATASET_ARTIFACTS = {
    "manifest": ("manifest_path", "application/json", "manifest.json"),
    "quality_report": ("quality_report_path", "application/json", "quality_report.json"),
    "data_contract": ("data_contract_path", "application/json", "data_contract.json"),
    "data": ("data_path", "application/x-ndjson", "data.jsonl"),
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

    def close(self) -> None:
        self.db.close()

    def create_user(
        self,
        email: str,
        password: str,
        roles: List[str],
        display_name: str = "",
        tenant_id: str = "default",
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        user_id = _id("usr")
        normalized = normalize_email(email)
        clean_roles = _clean_roles(roles)
        clean_tenant_id = _clean_tenant_id(tenant_id)
        now = _now()
        password_value = hash_password(password, pepper=self.settings.password_pepper)
        with self._session() as conn:
            self._ensure_tenant(conn, clean_tenant_id, clean_tenant_id, actor_id=actor_id)
            if self._get_one(conn, "SELECT id FROM users WHERE email = ?", (normalized,)):
                raise ValueError("user_email_exists")
            self._execute(
                conn,
                """
                INSERT INTO users
                (id, tenant_id, email, display_name, password_hash, roles_json, status, created_at, updated_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, clean_tenant_id, normalized, display_name, password_value, dumps(clean_roles), "active", now, now, None),
            )
            self._audit(conn, actor_id, "user.created", "user", user_id, {"email": normalized, "roles": clean_roles, "tenant_id": clean_tenant_id})
            return self.get_user(user_id, conn=conn)

    def create_tenant(self, tenant_id: str, name: str, actor_id: str = "system") -> Dict[str, Any]:
        clean_tenant_id = _clean_tenant_id(tenant_id)
        now = _now()
        with self._session() as conn:
            if self._get_one(conn, "SELECT id FROM tenants WHERE id = ?", (clean_tenant_id,)):
                raise ValueError("tenant_exists")
            self._execute(
                conn,
                """
                INSERT INTO tenants (id, name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (clean_tenant_id, name[:160] or clean_tenant_id, "active", now, now),
            )
            self._audit(conn, actor_id, "tenant.created", "tenant", clean_tenant_id, {"name": name[:160]})
            return self.get_tenant(clean_tenant_id, conn=conn)

    def get_tenant(self, tenant_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_tenant(tenant_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM tenants WHERE id = ?", (_clean_tenant_id(tenant_id),))
        if not row:
            raise KeyError("tenant_not_found")
        return row_to_dict(row)

    def list_tenants(self, limit: int = 100, offset: int = 0, status: Optional[str] = None) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        params: List[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                row_to_dict(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM tenants
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

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
                SELECT t.*, u.status AS user_status, u.tenant_id AS tenant_id
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
                "tenant_id": row["tenant_id"],
            }

    def create_authorization_snapshot(
        self,
        owner_id: str,
        allowed_uses: List[str],
        policy_version: str = "cn-pipl-2026-05",
        terms_version: str = "contributor-2026-05",
        source: str = "api",
        consent_text: str = "",
        actor_id: Optional[str] = None,
        conn: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.create_authorization_snapshot(
                    owner_id=owner_id,
                    allowed_uses=allowed_uses,
                    policy_version=policy_version,
                    terms_version=terms_version,
                    source=source,
                    consent_text=consent_text,
                    actor_id=actor_id,
                    conn=active,
                )

        snapshot_id = _id("authz")
        now = _now()
        scope = _clean_allowed_uses(allowed_uses)
        consent_text_hash = _sha256(consent_text) if consent_text else ""
        self._execute(
            conn,
            """
            INSERT INTO authorization_snapshots
            (id, owner_id, status, allowed_uses_json, policy_version, terms_version,
             consent_text_hash, source, created_at, withdrawn_at, withdrawal_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (snapshot_id, owner_id, "active", dumps(scope), policy_version, terms_version, consent_text_hash, source, now, None, ""),
        )
        self._audit(conn, actor_id or owner_id, "authorization.created", "authorization_snapshot", snapshot_id, {"allowed_uses": scope})
        return self.get_authorization_snapshot(snapshot_id, conn=conn)

    def get_authorization_snapshot(self, snapshot_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_authorization_snapshot(snapshot_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM authorization_snapshots WHERE id = ?", (snapshot_id,))
        if not row:
            raise KeyError("authorization_snapshot_not_found")
        return self._authorization_from_row(row)

    def list_authorization_snapshots(
        self,
        owner_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if owner_id:
            filters.append("owner_id = ?")
            params.append(owner_id)
        if status:
            filters.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._authorization_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM authorization_snapshots
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def withdraw_authorization_snapshot(self, snapshot_id: str, reason: str = "", actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
            snapshot = self.get_authorization_snapshot(snapshot_id, conn=conn)
            if snapshot["status"] == "withdrawn":
                return snapshot
            now = _now()
            self._execute(
                conn,
                """
                UPDATE authorization_snapshots
                SET status = ?, withdrawn_at = ?, withdrawal_reason = ?
                WHERE id = ?
                """,
                ("withdrawn", now, reason, snapshot_id),
            )
            self._execute(
                conn,
                """
                UPDATE cases
                SET status = ?, updated_at = ?
                WHERE authorization_snapshot_id = ? AND status != ?
                """,
                ("withdrawn", now, snapshot_id, "withdrawn"),
            )
            self._execute(
                conn,
                """
                UPDATE assets
                SET status = ?, updated_at = ?
                WHERE authorization_snapshot_id = ? AND status != ?
                """,
                ("withdrawn", now, snapshot_id, "withdrawn"),
            )
            self._audit(conn, actor_id, "authorization.withdrawn", "authorization_snapshot", snapshot_id, {"reason": reason[:400]})
            return self.get_authorization_snapshot(snapshot_id, conn=conn)

    def submit_text(
        self,
        owner_id: str,
        text: str,
        allowed_uses: List[str],
        actor_id: Optional[str] = None,
        enqueue: Optional[bool] = None,
        authorization_snapshot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        submission_id = _id("sub")
        raw_hash = _sha256(text)
        now = _now()
        raw_expires_at = _future_hours(self.settings.raw_object_ttl_hours)
        raw_ref = None
        committed = False
        try:
            with self._session() as conn:
                authorization = self._resolve_authorization(
                    conn,
                    owner_id=owner_id,
                    allowed_uses=allowed_uses,
                    authorization_snapshot_id=authorization_snapshot_id,
                    actor_id=actor_id or owner_id,
                    source="text_submission",
                )
                raw_ref = self.objects.put_text(f"raw/{submission_id}.txt", text)
                self._execute(
                    conn,
                    """
                    INSERT INTO submissions
                    (id, owner_id, source_type, status, raw_path, raw_hash, allowed_uses_json,
                     authorization_snapshot_id, raw_expires_at, raw_deleted_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        submission_id,
                        owner_id,
                        "text",
                        "quarantined",
                        raw_ref.uri,
                        raw_hash,
                        dumps(authorization["allowed_uses"]),
                        authorization["id"],
                        raw_expires_at,
                        None,
                        now,
                    ),
                )
                self._audit(
                    conn,
                    actor_id or owner_id,
                    "submission.created",
                    "submission",
                    submission_id,
                    {"source_type": "text", "authorization_snapshot_id": authorization["id"]},
                )

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
            committed = True
        except Exception:
            if raw_ref and not committed:
                self._delete_object_quietly(raw_ref.uri)
            raise
        if queued:
            self._publish_job("ingestion", job_id)
            return queued

        processed = self.process_submission(submission_id, actor_id=actor_id or owner_id)
        return {"submission_id": submission_id, "case": processed}

    def submit_asset(
        self,
        owner_id: str,
        filename: str,
        media_type: str,
        content: bytes,
        allowed_uses: List[str],
        actor_id: Optional[str] = None,
        enqueue: Optional[bool] = None,
        authorization_snapshot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not content:
            raise ValueError("asset_empty")
        if len(content) > self.settings.max_asset_bytes:
            raise ValueError("asset_too_large")
        asset_id = _id("ast")
        inspection = inspect_asset(filename, media_type, content)
        now = _now()
        raw_expires_at = _future_hours(self.settings.raw_object_ttl_hours)
        raw_ref = None
        committed = False
        try:
            with self._session() as conn:
                authorization = self._resolve_authorization(
                    conn,
                    owner_id=owner_id,
                    allowed_uses=allowed_uses,
                    authorization_snapshot_id=authorization_snapshot_id,
                    actor_id=actor_id or owner_id,
                    source="asset_upload",
                )
                raw_ref = self.objects.put_bytes(
                    f"raw/assets/{asset_id}/{inspection.filename}",
                    content,
                    inspection.media_type,
                )
                self._execute(
                    conn,
                    """
                    INSERT INTO assets
                    (id, owner_id, submission_id, authorization_snapshot_id, filename, media_type, asset_type,
                     byte_size, sha256, status, raw_path, extracted_text_path, metadata_json, risk_json,
                     redaction_json, raw_expires_at, raw_deleted_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset_id,
                        owner_id,
                        None,
                        authorization["id"],
                        inspection.filename,
                        inspection.media_type,
                        inspection.asset_type,
                        inspection.byte_size,
                        inspection.sha256,
                        "quarantined",
                        raw_ref.uri,
                        None,
                        dumps(inspection.metadata),
                        dumps(inspection.risk),
                        dumps(inspection.redaction or {}),
                        raw_expires_at,
                        None,
                        now,
                        now,
                    ),
                )
                self._audit(
                    conn,
                    actor_id or owner_id,
                    "asset.created",
                    "asset",
                    asset_id,
                    {"asset_type": inspection.asset_type, "authorization_snapshot_id": authorization["id"]},
                )
                should_enqueue = self.settings.async_processing if enqueue is None else enqueue
                if should_enqueue:
                    job_id = self._enqueue_job(
                        conn,
                        job_type="process_asset",
                        payload={"asset_id": asset_id},
                        queue_name="ingestion",
                        actor_id=actor_id or owner_id,
                    )
                    asset = self.get_asset(asset_id, conn=conn)
                else:
                    job_id = ""
                    asset = self.get_asset(asset_id, conn=conn)
            committed = True
        except Exception:
            if raw_ref and not committed:
                self._delete_object_quietly(raw_ref.uri)
            raise
        if job_id:
            self._publish_job("ingestion", job_id)
            return {"asset": asset, "status": "queued"}
        return {"asset": self.process_asset(asset_id, actor_id=actor_id or owner_id)}

    def create_asset_upload_session(
        self,
        owner_id: str,
        filename: str,
        media_type: str,
        byte_size: int,
        allowed_uses: List[str],
        actor_id: Optional[str] = None,
        authorization_snapshot_id: Optional[str] = None,
        expires_in_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        if byte_size <= 0:
            raise ValueError("asset_empty")
        if byte_size > self.settings.max_asset_bytes:
            raise ValueError("asset_too_large")
        expires_in = max(60, min(expires_in_seconds or self.settings.upload_session_ttl_seconds, 86_400))
        asset_id = _id("ast")
        session_id = _id("upl")
        clean_filename = sanitize_filename(filename)
        clean_media_type = (media_type or "application/octet-stream").split(";", 1)[0].strip().lower() or "application/octet-stream"
        object_key = f"raw/assets/{asset_id}/{clean_filename}"
        now = _now()
        with self._session() as conn:
            authorization = self._resolve_authorization(
                conn,
                owner_id=owner_id,
                allowed_uses=allowed_uses,
                authorization_snapshot_id=authorization_snapshot_id,
                actor_id=actor_id or owner_id,
                source="asset_direct_upload",
            )
            upload = self.objects.presign_put(object_key, clean_media_type, expires_in)
            self._execute(
                conn,
                """
                INSERT INTO asset_upload_sessions
                (id, asset_id, owner_id, authorization_snapshot_id, filename, media_type, expected_byte_size,
                 object_key, object_uri, status, allowed_uses_json, expires_at, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    asset_id,
                    owner_id,
                    authorization["id"],
                    clean_filename,
                    clean_media_type,
                    byte_size,
                    upload["object_key"],
                    upload["object_uri"],
                    "pending",
                    dumps(authorization["allowed_uses"]),
                    _future_seconds(expires_in),
                    now,
                    None,
                ),
            )
            self._audit(
                conn,
                actor_id or owner_id,
                "asset_upload_session.created",
                "asset_upload_session",
                session_id,
                {"asset_id": asset_id, "direct_upload_supported": upload["direct_upload_supported"]},
            )
            session = self.get_asset_upload_session(session_id, conn=conn)
        return {"session": session, "upload": upload}

    def complete_asset_upload_session(
        self,
        session_id: str,
        actor_id: str = "system",
        enqueue: Optional[bool] = None,
    ) -> Dict[str, Any]:
        with self._session() as conn:
            session = self.get_asset_upload_session(session_id, conn=conn)
            if session["status"] != "pending":
                raise ValueError("upload_session_not_pending")
            if _is_expired(session["expires_at"]):
                self._execute(conn, "UPDATE asset_upload_sessions SET status = ? WHERE id = ?", ("expired", session_id))
                raise ValueError("upload_session_expired")
            authorization = self.get_authorization_snapshot(session["authorization_snapshot_id"], conn=conn)
            if authorization["status"] != "active":
                raise ValueError("authorization_not_active")
            cursor = self._execute(
                conn,
                "UPDATE asset_upload_sessions SET status = ? WHERE id = ? AND status = ?",
                ("processing", session_id, "pending"),
            )
            if cursor.rowcount == 0:
                raise ValueError("upload_session_not_pending")

        try:
            try:
                content = self.objects.read_bytes(session["object_uri"])
            except Exception as exc:
                raise ValueError("upload_object_not_readable") from exc
            if not content:
                raise ValueError("asset_empty")
            if len(content) > self.settings.max_asset_bytes:
                raise ValueError("asset_too_large")
            if session["expected_byte_size"] and len(content) != session["expected_byte_size"]:
                raise ValueError("asset_size_mismatch")
            inspection = inspect_asset(session["filename"], session["media_type"], content)
        except Exception:
            with self._session() as conn:
                self._execute(
                    conn,
                    "UPDATE asset_upload_sessions SET status = ? WHERE id = ? AND status = ?",
                    ("pending", session_id, "processing"),
                )
            raise

        try:
            with self._session() as conn:
                session = self.get_asset_upload_session(session_id, conn=conn)
                if session["status"] != "processing":
                    raise ValueError("upload_session_not_pending")
                authorization = self.get_authorization_snapshot(session["authorization_snapshot_id"], conn=conn)
                if authorization["status"] != "active":
                    raise ValueError("authorization_not_active")
                now = _now()
                raw_expires_at = _future_hours(self.settings.raw_object_ttl_hours)
                self._execute(
                    conn,
                    """
                    INSERT INTO assets
                    (id, owner_id, submission_id, authorization_snapshot_id, filename, media_type, asset_type,
                     byte_size, sha256, status, raw_path, extracted_text_path, metadata_json, risk_json,
                     redaction_json, raw_expires_at, raw_deleted_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["asset_id"],
                        session["owner_id"],
                        None,
                        authorization["id"],
                        inspection.filename,
                        inspection.media_type,
                        inspection.asset_type,
                        inspection.byte_size,
                        inspection.sha256,
                        "quarantined",
                        session["object_uri"],
                        None,
                        dumps(inspection.metadata),
                        dumps(inspection.risk),
                        dumps(inspection.redaction or {}),
                        raw_expires_at,
                        None,
                        now,
                        now,
                    ),
                )
                self._execute(
                    conn,
                    "UPDATE asset_upload_sessions SET status = ?, completed_at = ? WHERE id = ?",
                    ("completed", now, session_id),
                )
                self._audit(
                    conn,
                    actor_id,
                    "asset_upload_session.completed",
                    "asset_upload_session",
                    session_id,
                    {"asset_id": session["asset_id"], "asset_type": inspection.asset_type},
                )
                should_enqueue = self.settings.async_processing if enqueue is None else enqueue
                if should_enqueue:
                    job_id = self._enqueue_job(
                        conn,
                        job_type="process_asset",
                        payload={"asset_id": session["asset_id"]},
                        queue_name="ingestion",
                        actor_id=actor_id,
                    )
                    asset = self.get_asset(session["asset_id"], conn=conn)
                else:
                    job_id = ""
                    asset = self.get_asset(session["asset_id"], conn=conn)
        except Exception:
            with self._session() as conn:
                self._execute(
                    conn,
                    "UPDATE asset_upload_sessions SET status = ? WHERE id = ? AND status = ?",
                    ("pending", session_id, "processing"),
                )
            raise
        if job_id:
            self._publish_job("ingestion", job_id)
            return {"asset": asset, "status": "queued"}
        return {"asset": self.process_asset(session["asset_id"], actor_id=actor_id)}

    def get_asset_upload_session(self, session_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_asset_upload_session(session_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM asset_upload_sessions WHERE id = ?", (session_id,))
        if not row:
            raise KeyError("upload_session_not_found")
        return self._asset_upload_session_from_row(row)

    def process_asset(self, asset_id: str, actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
            asset_row = self._get_one(conn, "SELECT * FROM assets WHERE id = ?", (asset_id,))
            if not asset_row:
                raise KeyError("asset_not_found")
            if asset_row["raw_deleted_at"]:
                raise ValueError("raw_object_deleted")
            authorization = self.get_authorization_snapshot(asset_row["authorization_snapshot_id"], conn=conn)
            if authorization["status"] != "active":
                raise ValueError("authorization_not_active")
            content = self.objects.read_bytes(asset_row["raw_path"])
            inspection = inspect_asset(asset_row["filename"], asset_row["media_type"], content)
            extracted_ref = None
            submission_id = asset_row["submission_id"]
            if inspection.extracted_text:
                extracted_text = inspection.redaction["redacted_text"] if inspection.redaction else inspection.extracted_text
                extracted_ref = self.objects.put_text(f"evidence/assets/{asset_id}/redacted_text.txt", extracted_text)
                if not submission_id and inspection.status == "evidence_ready":
                    submission_id = self._create_submission_from_asset(
                        conn,
                        asset_id=asset_id,
                        owner_id=asset_row["owner_id"],
                        text=inspection.extracted_text,
                        allowed_uses=authorization["allowed_uses"],
                        authorization_snapshot_id=authorization["id"],
                        actor_id=actor_id,
                    )
            now = _now()
            self._execute(
                conn,
                """
                UPDATE assets
                SET submission_id = ?, asset_type = ?, byte_size = ?, sha256 = ?, status = ?,
                    extracted_text_path = ?, metadata_json = ?, risk_json = ?, redaction_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    submission_id,
                    inspection.asset_type,
                    inspection.byte_size,
                    inspection.sha256,
                    inspection.status,
                    extracted_ref.uri if extracted_ref else asset_row["extracted_text_path"],
                    dumps(inspection.metadata),
                    dumps(inspection.risk),
                    dumps(inspection.redaction or {}),
                    now,
                    asset_id,
                ),
            )
            self._audit(conn, actor_id, "asset.processed", "asset", asset_id, {"status": inspection.status})
        if submission_id and inspection.status == "evidence_ready":
            try:
                self.process_submission(submission_id, actor_id=actor_id)
            except ValueError as exc:
                if str(exc) != "raw_object_deleted":
                    raise
        return self.get_asset(asset_id)

    def request_asset_extraction(self, asset_id: str, actor_id: str = "system", queue_name: str = "extraction") -> Dict[str, Any]:
        with self._session() as conn:
            asset = self.get_asset(asset_id, conn=conn)
            if asset["status"] not in {"extraction_pending", "manual_review"}:
                raise ValueError("asset_not_extractable")
            job_id = self._enqueue_job(
                conn,
                job_type="extract_asset",
                payload={"asset_id": asset_id},
                queue_name=queue_name,
                actor_id=actor_id,
            )
            job = self.get_job(job_id, conn=conn)
        self._publish_job(queue_name, job_id)
        return job

    def process_asset_extraction(self, asset_id: str, actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
            asset_row = self._get_one(conn, "SELECT * FROM assets WHERE id = ?", (asset_id,))
            if not asset_row:
                raise KeyError("asset_not_found")
            if asset_row["raw_deleted_at"]:
                raise ValueError("raw_object_deleted")
            authorization = self.get_authorization_snapshot(asset_row["authorization_snapshot_id"], conn=conn)
            if authorization["status"] != "active":
                raise ValueError("authorization_not_active")
            content = self.objects.read_bytes(asset_row["raw_path"])
            result = extract_asset_text(asset_row["asset_type"], asset_row["media_type"], content)
            invocation = extraction_invocation(asset_id, result)
            invocation["input_hash"] = _sha256_bytes(content)
            self._record_model_invocation(conn, invocation)

            if result.status != "succeeded" or not result.text:
                now = _now()
                metadata = loads(asset_row["metadata_json"])
                metadata["extraction"] = result.metadata
                self._execute(
                    conn,
                    """
                    UPDATE assets
                    SET status = ?, metadata_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("extraction_pending", dumps(metadata), now, asset_id),
                )
                self._audit(conn, actor_id, "asset.extraction_deferred", "asset", asset_id, {"reason": result.error})
                return self.get_asset(asset_id, conn=conn)

            redaction = to_jsonable(redact_text(result.text))
            extracted_text = redaction["redacted_text"]
            extracted_ref = self.objects.put_text(f"evidence/assets/{asset_id}/extracted_text.txt", extracted_text)
            submission_id = asset_row["submission_id"]
            if not submission_id and redaction.get("passed", False):
                submission_id = self._create_submission_from_asset(
                    conn,
                    asset_id=asset_id,
                    owner_id=asset_row["owner_id"],
                    text=result.text,
                    allowed_uses=authorization["allowed_uses"],
                    authorization_snapshot_id=authorization["id"],
                    actor_id=actor_id,
                )
            status = "evidence_ready" if redaction.get("passed", False) else "privacy_review"
            metadata = loads(asset_row["metadata_json"])
            metadata["extraction"] = result.metadata
            now = _now()
            self._execute(
                conn,
                """
                UPDATE assets
                SET submission_id = ?, status = ?, extracted_text_path = ?, metadata_json = ?,
                    redaction_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (submission_id, status, extracted_ref.uri, dumps(metadata), dumps(redaction), now, asset_id),
            )
            self._audit(conn, actor_id, "asset.extracted", "asset", asset_id, {"status": status, "provider": result.provider})
        if submission_id and status == "evidence_ready":
            self.process_submission(submission_id, actor_id=actor_id)
        return self.get_asset(asset_id)

    def get_asset(self, asset_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_asset(asset_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM assets WHERE id = ?", (asset_id,))
        if not row:
            raise KeyError("asset_not_found")
        return self._asset_from_row(row)

    def list_assets(
        self,
        owner_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if owner_id:
            filters.append("owner_id = ?")
            params.append(owner_id)
        if status:
            filters.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._asset_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM assets
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def process_submission(self, submission_id: str, actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
            submission = self._get_one(conn, "SELECT * FROM submissions WHERE id = ?", (submission_id,))
            if not submission:
                raise KeyError("submission_not_found")
            if submission["raw_deleted_at"]:
                raise ValueError("raw_object_deleted")
            if submission["authorization_snapshot_id"]:
                authorization = self.get_authorization_snapshot(submission["authorization_snapshot_id"], conn=conn)
                if authorization["status"] != "active":
                    raise ValueError("authorization_not_active")
            existing_case = self._get_one(conn, "SELECT * FROM cases WHERE submission_id = ?", (submission_id,))
            if existing_case:
                self._audit(conn, actor_id, "case.process_skipped", "case", existing_case["id"], {"reason": "already_processed"})
                return self._case_from_row(existing_case)
            raw_text = self.objects.read_text(submission["raw_path"])
            existing_fingerprints = [
                row_to_dict(row)
                for row in self._execute(conn, "SELECT canonical_hash, dedup_json FROM cases")
            ]
            known_hashes = [row["canonical_hash"] for row in existing_fingerprints]
            known_simhashes = [
                simhash
                for row in existing_fingerprints
                if (simhash := _simhash_from_dedup_json(row.get("dedup_json"))) is not None
            ]
            allowed_uses = loads(submission["allowed_uses_json"])
            processed = process_text_case(
                raw_text=raw_text,
                owner_id=submission["owner_id"],
                allowed_uses=allowed_uses,
                known_hashes=known_hashes,
                known_simhashes=known_simhashes,
            )
            case = to_jsonable(processed.case)
            now = _now()
            self._execute(
                conn,
                """
                INSERT INTO cases
                (id, submission_id, owner_id, status, redacted_text, raw_hash, canonical_hash,
                 drl, quality_score, redaction_json, annotation_json, dedup_json, quality_gate_json,
                 authorization_snapshot_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    submission["authorization_snapshot_id"],
                    now,
                    now,
                ),
            )
            self._execute(conn, "UPDATE submissions SET status = ? WHERE id = ?", (processed.status, submission_id))
            self._audit(conn, actor_id, "case.processed", "case", case["case_id"], {"status": processed.status})
            stored_case = self.get_case(case["case_id"], conn=conn)
            self._record_model_invocation(conn, annotation_invocation(stored_case))
            return stored_case

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

    def contributor_dashboard(self, contributor_id: str, limit: int = 10) -> Dict[str, Any]:
        limit = _bounded_limit(limit, self.settings.max_page_limit)
        with self._session() as conn:
            recent_cases = [
                self._case_from_row(row)
                for row in self._execute(
                    conn,
                    """
                    SELECT * FROM cases
                    WHERE owner_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (contributor_id, limit),
                )
            ]
            recent_payouts = [
                row_to_dict(row)
                for row in self._execute(
                    conn,
                    """
                    SELECT * FROM payout_events
                    WHERE contributor_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (contributor_id, self.settings.max_page_limit),
                )
            ]
            payout_totals = _payout_totals_by_status(conn, self, contributor_id)
            return {
                "contributor_id": contributor_id,
                "cases": {
                    "total": _owner_count(conn, self, "cases", contributor_id),
                    "by_status": _owner_count_grouped(conn, self, "cases", "status", contributor_id),
                    "by_drl": _owner_count_grouped(conn, self, "cases", "drl", contributor_id),
                    "recent": recent_cases,
                },
                "assets": {
                    "total": _owner_count(conn, self, "assets", contributor_id),
                    "by_status": _owner_count_grouped(conn, self, "assets", "status", contributor_id),
                },
                "authorizations": {
                    "total": _owner_count(conn, self, "authorization_snapshots", contributor_id),
                    "by_status": _owner_count_grouped(conn, self, "authorization_snapshots", "status", contributor_id),
                },
                "ledger": {
                    "pending_cents": payout_totals["amounts"].get("pending", 0),
                    "batched_cents": payout_totals["amounts"].get("batched", 0),
                    "settled_cents": payout_totals["amounts"].get("settled", 0),
                    "total_cents": sum(payout_totals["amounts"].values()),
                    "payout_count": payout_totals["total_count"],
                    "recent": recent_payouts[:limit],
                },
            }

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
            if case["status"] == "withdrawn":
                raise ValueError("case_withdrawn")
            if case["status"] == "rejected":
                raise ValueError("case_rejected")
            _assert_review_claim(case, reviewer_id)
            if not self._case_authorization_active(conn, case):
                raise ValueError("authorization_not_active")
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
                SET status = ?, drl = ?, quality_gate_json = ?, review_claimed_by = NULL, review_claimed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status, "DRL3", dumps(gate), _now(), case_id),
            )
            self._execute(
                conn,
                """
                INSERT INTO reviews
                (id, case_id, reviewer_id, review_type, decision, score, notes, rubric_json, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_id("rev"), case_id, reviewer_id, "human", "approved", 1.0, notes, dumps({}), dumps({}), _now()),
            )
            self._audit(conn, reviewer_id, "case.review_approved", "case", case_id, {"drl": "DRL3"})
            return self.get_case(case_id, conn=conn)

    def expert_verify_case(
        self,
        case_id: str,
        reviewer_id: str,
        notes: str = "",
        rubric: Optional[Dict[str, Any]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        score: float = 1.0,
    ) -> Dict[str, Any]:
        with self._session() as conn:
            case = self.get_case(case_id, conn=conn)
            if case["status"] == "withdrawn":
                raise ValueError("case_withdrawn")
            if case["status"] == "rejected":
                raise ValueError("case_rejected")
            _assert_review_claim(case, reviewer_id)
            if not self._case_authorization_active(conn, case):
                raise ValueError("authorization_not_active")
            if DRL_ORDER.get(case["quality_gate"]["drl"], 0) < DRL_ORDER["DRL3"]:
                raise ValueError("human_review_required")
            if "training" not in case["quality_gate"]["allowed_uses"]:
                raise ValueError("training_use_not_authorized")
            gate = dict(case["quality_gate"])
            gate["drl"] = "DRL4"
            gate["commercial_ready"] = True
            gate["required_actions"] = [action for action in gate.get("required_actions", []) if action != "expert_review"]
            self._execute(
                conn,
                """
                INSERT INTO reviews
                (id, case_id, reviewer_id, review_type, decision, score, notes, rubric_json, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_id("rev"), case_id, reviewer_id, "expert", "approved", _bounded_score(score), notes, dumps(rubric or {}), dumps(evidence or {}), _now()),
            )
            self._execute(
                conn,
                """
                UPDATE cases
                SET status = ?, drl = ?, quality_gate_json = ?, review_claimed_by = NULL, review_claimed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                ("commercial_ready", "DRL4", dumps(gate), _now(), case_id),
            )
            self._audit(conn, reviewer_id, "case.expert_verified", "case", case_id, {"drl": "DRL4"})
            return self.get_case(case_id, conn=conn)

    def gold_review_case(
        self,
        case_id: str,
        reviewer_id: str,
        notes: str = "",
        rubric: Optional[Dict[str, Any]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        score: float = 1.0,
    ) -> Dict[str, Any]:
        with self._session() as conn:
            case = self.get_case(case_id, conn=conn)
            if case["status"] == "withdrawn":
                raise ValueError("case_withdrawn")
            if case["status"] == "rejected":
                raise ValueError("case_rejected")
            _assert_review_claim(case, reviewer_id)
            if not self._case_authorization_active(conn, case):
                raise ValueError("authorization_not_active")
            if "gold_eval" not in case["quality_gate"]["allowed_uses"]:
                raise ValueError("gold_eval_not_authorized")
            if DRL_ORDER.get(case["quality_gate"]["drl"], 0) < DRL_ORDER["DRL4"]:
                raise ValueError("expert_review_required")
            existing_reviewers = {
                row["reviewer_id"]
                for row in self._execute(
                    conn,
                    """
                    SELECT reviewer_id FROM reviews
                    WHERE case_id = ? AND review_type = ? AND decision = ?
                    """,
                    (case_id, "gold", "approved"),
                )
            }
            if reviewer_id in existing_reviewers:
                raise ValueError("gold_reviewer_duplicate")
            self._execute(
                conn,
                """
                INSERT INTO reviews
                (id, case_id, reviewer_id, review_type, decision, score, notes, rubric_json, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_id("rev"), case_id, reviewer_id, "gold", "approved", _bounded_score(score), notes, dumps(rubric or {}), dumps(evidence or {}), _now()),
            )
            reviewers_after = len(existing_reviewers) + 1
            gate = dict(case["quality_gate"])
            if reviewers_after >= 2:
                gate["drl"] = "DRL5"
                gate["commercial_ready"] = True
                gate["required_actions"] = [action for action in gate.get("required_actions", []) if action != "gold_second_review"]
                drl = "DRL5"
            else:
                gate["drl"] = "DRL4"
                gate["commercial_ready"] = True
                required = list(gate.get("required_actions", []))
                if "gold_second_review" not in required:
                    required.append("gold_second_review")
                gate["required_actions"] = required
                drl = "DRL4"
            self._execute(
                conn,
                """
                UPDATE cases
                SET status = ?, drl = ?, quality_gate_json = ?, review_claimed_by = NULL, review_claimed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                ("commercial_ready", drl, dumps(gate), _now(), case_id),
            )
            self._audit(conn, reviewer_id, "case.gold_reviewed", "case", case_id, {"drl": drl, "reviewers": reviewers_after})
            return self.get_case(case_id, conn=conn)

    def reject_case(self, case_id: str, reviewer_id: str, reason: str = "") -> Dict[str, Any]:
        with self._session() as conn:
            case = self.get_case(case_id, conn=conn)
            if case["status"] == "withdrawn":
                raise ValueError("case_withdrawn")
            _assert_review_claim(case, reviewer_id)
            gate = dict(case["quality_gate"])
            required_actions = list(gate.get("required_actions") or [])
            if "rejected" not in required_actions:
                required_actions.append("rejected")
            gate["required_actions"] = required_actions
            gate["commercial_ready"] = False
            self._execute(
                conn,
                """
                UPDATE cases
                SET status = ?, quality_gate_json = ?, review_claimed_by = NULL, review_claimed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                ("rejected", dumps(gate), _now(), case_id),
            )
            self._execute(
                conn,
                """
                INSERT INTO reviews
                (id, case_id, reviewer_id, review_type, decision, score, notes, rubric_json, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (_id("rev"), case_id, reviewer_id, "human", "rejected", 0.0, reason, dumps({}), dumps({}), _now()),
            )
            self._audit(conn, reviewer_id, "case.review_rejected", "case", case_id, {"reason": reason[:400]})
            return self.get_case(case_id, conn=conn)

    def list_reviews(
        self,
        case_id: Optional[str] = None,
        review_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if case_id:
            filters.append("case_id = ?")
            params.append(case_id)
        if review_type:
            filters.append("review_type = ?")
            params.append(review_type)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._review_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM reviews
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

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

    def claim_review_case(self, reviewer_id: str, case_id: Optional[str] = None) -> Dict[str, Any]:
        now = _now()
        with self._session() as conn:
            if case_id:
                case = self.get_case(case_id, conn=conn)
                if case["status"] not in {"review_pending", "privacy_review"}:
                    raise ValueError("case_not_reviewable")
                if case.get("review_claimed_by") and case["review_claimed_by"] != reviewer_id:
                    raise ValueError("review_case_claimed_by_other")
                cursor = self._execute(
                    conn,
                    """
                    UPDATE cases
                    SET review_claimed_by = ?, review_claimed_at = ?, updated_at = ?
                    WHERE id = ? AND (review_claimed_by IS NULL OR review_claimed_by = ?)
                    """,
                    (reviewer_id, now, now, case_id, reviewer_id),
                )
                if cursor.rowcount == 0:
                    raise ValueError("review_case_claimed_by_other")
                self._audit(conn, reviewer_id, "review.claimed", "case", case_id, {})
                return self.get_case(case_id, conn=conn)

            if self.db.use_postgres:
                row = self._get_one(
                    conn,
                    """
                    UPDATE cases
                    SET review_claimed_by = ?, review_claimed_at = ?, updated_at = ?
                    WHERE id = (
                        SELECT id FROM cases
                        WHERE status IN (?, ?) AND review_claimed_by IS NULL
                        ORDER BY quality_score DESC, created_at ASC, id ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *
                    """,
                    (reviewer_id, now, now, "review_pending", "privacy_review"),
                )
                if not row:
                    raise ValueError("no_review_cases_available")
                case = self._case_from_row(row)
                self._audit(conn, reviewer_id, "review.claimed", "case", case["case_id"], {})
                return case

            row = self._get_one(
                conn,
                """
                SELECT * FROM cases
                WHERE status IN (?, ?) AND review_claimed_by IS NULL
                ORDER BY quality_score DESC, created_at ASC, id ASC
                LIMIT 1
                """,
                ("review_pending", "privacy_review"),
            )
            if not row:
                raise ValueError("no_review_cases_available")
            case_id = row["id"]
            cursor = self._execute(
                conn,
                """
                UPDATE cases
                SET review_claimed_by = ?, review_claimed_at = ?, updated_at = ?
                WHERE id = ? AND review_claimed_by IS NULL
                """,
                (reviewer_id, now, now, case_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("review_case_claimed_by_other")
            self._audit(conn, reviewer_id, "review.claimed", "case", case_id, {})
            return self.get_case(case_id, conn=conn)

    def release_review_case(self, case_id: str, reviewer_id: str, force: bool = False) -> Dict[str, Any]:
        with self._session() as conn:
            case = self.get_case(case_id, conn=conn)
            if case.get("review_claimed_by") != reviewer_id and not force:
                raise ValueError("review_case_not_claimed_by_actor")
            self._execute(
                conn,
                """
                UPDATE cases
                SET review_claimed_by = NULL, review_claimed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (_now(), case_id),
            )
            self._audit(conn, reviewer_id, "review.released", "case", case_id, {"force": force})
            return self.get_case(case_id, conn=conn)

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
        if purpose == "gold_eval" and min_drl != "DRL5":
            raise ValueError("gold_eval_requires_drl5")
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
                    WHERE status = ? AND drl >= ?
                    ORDER BY quality_score DESC, created_at ASC, id ASC
                    LIMIT ?
                    """,
                    ("commercial_ready", min_drl, max_cases),
                )
                if DRL_ORDER.get(loads(row["quality_gate_json"])["drl"], 0) >= DRL_ORDER[min_drl]
            ]
            eligible = [case for case in eligible if _case_allowed_for_purpose(case, purpose)]
            eligible = [case for case in eligible if self._case_authorization_active(conn, case)]
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

    def list_datasets(
        self,
        limit: int = 100,
        offset: int = 0,
        purpose: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if purpose:
            filters.append("purpose = ?")
            params.append(purpose)
        if status:
            filters.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            datasets = [
                row_to_dict(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM datasets
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]
            for dataset in datasets:
                dataset["case_ids"] = [
                    item["case_id"]
                    for item in self._execute(conn, "SELECT case_id FROM dataset_cases WHERE dataset_id = ?", (dataset["id"],))
                ]
            return datasets

    def read_dataset_artifact(self, dataset_id: str, artifact: str, actor_id: str = "system") -> Dict[str, Any]:
        column_name, media_type, filename = _dataset_artifact_descriptor(artifact)
        dataset = self.get_dataset(dataset_id)
        uri = dataset.get(column_name)
        if not uri:
            raise ValueError("dataset_artifact_missing")
        content = self.objects.read_text(uri)
        with self._session() as conn:
            self._audit(conn, actor_id, "dataset.artifact_read", "dataset", dataset_id, {"artifact": artifact})
        return {
            "dataset_id": dataset_id,
            "artifact": artifact,
            "filename": filename,
            "media_type": media_type,
            "content": content,
        }

    def create_enterprise_customer(
        self,
        name: str,
        contact_email: str,
        tenant_id: str = "default",
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        customer_id = _id("cust")
        clean_tenant_id = _clean_tenant_id(tenant_id)
        email = normalize_email(contact_email)
        if not _valid_contact_email(email):
            raise ValueError("invalid_contact_email")
        customer_name = (name or "").strip()
        if not customer_name:
            raise ValueError("enterprise_customer_name_required")
        now = _now()
        with self._session() as conn:
            self._ensure_tenant(conn, clean_tenant_id, clean_tenant_id, actor_id=actor_id)
            self._execute(
                conn,
                """
                INSERT INTO enterprise_customers
                (id, tenant_id, name, contact_email_hash, contact_email_domain, status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    clean_tenant_id,
                    customer_name[:160],
                    _sha256(email),
                    _email_domain(email),
                    "active",
                    actor_id,
                    now,
                    now,
                ),
            )
            self._audit(conn, actor_id, "enterprise_customer.created", "enterprise_customer", customer_id, {"tenant_id": clean_tenant_id})
            return self.get_enterprise_customer(customer_id, conn=conn)

    def get_enterprise_customer(self, customer_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_enterprise_customer(customer_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM enterprise_customers WHERE id = ?", (customer_id,))
        if not row:
            raise KeyError("enterprise_customer_not_found")
        return self._enterprise_customer_from_row(row)

    def list_enterprise_customers(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        params: List[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._enterprise_customer_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM enterprise_customers
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def create_enterprise_contract(
        self,
        customer_id: str,
        terms_version: str = "enterprise-contract-2026-05",
        terms: Optional[Dict[str, Any]] = None,
        expires_at: Optional[str] = None,
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        contract_id = _id("ect")
        now = _now()
        expires_at_value = _normalize_expires_at(expires_at) or _future_hours(24 * 365)
        with self._session() as conn:
            customer = self.get_enterprise_customer(customer_id, conn=conn)
            if customer["status"] != "active":
                raise ValueError("enterprise_customer_not_active")
            clean_terms = terms or {}
            self._execute(
                conn,
                """
                INSERT INTO enterprise_contracts
                (id, tenant_id, customer_id, version, status, terms_json, effective_at,
                 expires_at, signed_by, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract_id,
                    customer["tenant_id"],
                    customer_id,
                    terms_version[:80] or "enterprise-contract-2026-05",
                    "active",
                    dumps(clean_terms),
                    now,
                    expires_at_value,
                    actor_id,
                    actor_id,
                    now,
                    now,
                ),
            )
            self._audit(conn, actor_id, "enterprise_contract.created", "enterprise_contract", contract_id, {"customer_id": customer_id})
            return self.get_enterprise_contract(contract_id, conn=conn)

    def get_enterprise_contract(self, contract_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_enterprise_contract(contract_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM enterprise_contracts WHERE id = ?", (contract_id,))
        if not row:
            raise KeyError("enterprise_contract_not_found")
        return self._enterprise_contract_from_row(row)

    def list_enterprise_contracts(
        self,
        limit: int = 100,
        offset: int = 0,
        customer_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if customer_id:
            filters.append("customer_id = ?")
            params.append(customer_id)
        if status:
            filters.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._enterprise_contract_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM enterprise_contracts
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def create_enterprise_order(
        self,
        customer_id: str,
        dataset_id: str,
        contract_id: str,
        gross_revenue_cents: int,
        direct_cost_cents: int = 0,
        currency: str = "CNY",
        max_reads: int = 100,
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        order_id = _id("ord")
        max_reads = _bounded_limit(max_reads, 10_000)
        gross_revenue_cents = max(0, int(gross_revenue_cents))
        direct_cost_cents = max(0, int(direct_cost_cents))
        now = _now()
        with self._session() as conn:
            customer = self.get_enterprise_customer(customer_id, conn=conn)
            if customer["status"] != "active":
                raise ValueError("enterprise_customer_not_active")
            contract = self.get_enterprise_contract(contract_id, conn=conn)
            if contract["customer_id"] != customer_id or contract["tenant_id"] != customer["tenant_id"]:
                raise ValueError("enterprise_contract_scope_mismatch")
            if contract["status"] != "active" or _is_expired(contract["expires_at"]):
                raise ValueError("enterprise_contract_not_active")
            dataset = self.get_dataset(dataset_id, conn=conn)
            if dataset["status"] != "ready":
                raise ValueError("dataset_not_ready")
            self._assert_tenant_order_quota(conn, customer["tenant_id"])
            self._execute(
                conn,
                """
                INSERT INTO enterprise_orders
                (id, tenant_id, customer_id, dataset_id, contract_id, status, gross_revenue_cents,
                 direct_cost_cents, currency, max_reads, usage_event_id, delivery_grant_id,
                 created_by, created_at, updated_at, recognized_at, last_delivery_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    customer["tenant_id"],
                    customer_id,
                    dataset_id,
                    contract_id,
                    "ready",
                    gross_revenue_cents,
                    direct_cost_cents,
                    currency[:12].upper() or "CNY",
                    max_reads,
                    "",
                    "",
                    actor_id,
                    now,
                    now,
                    None,
                    None,
                ),
            )
            self._audit(conn, actor_id, "enterprise_order.created", "enterprise_order", order_id, {"dataset_id": dataset_id, "customer_id": customer_id})
            return self.get_enterprise_order(order_id, conn=conn)

    def get_enterprise_order(self, order_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_enterprise_order(order_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM enterprise_orders WHERE id = ?", (order_id,))
        if not row:
            raise KeyError("enterprise_order_not_found")
        return self._enterprise_order_from_row(row)

    def list_enterprise_orders(
        self,
        limit: int = 100,
        offset: int = 0,
        customer_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if customer_id:
            filters.append("customer_id = ?")
            params.append(customer_id)
        if status:
            filters.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._enterprise_order_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM enterprise_orders
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def recognize_enterprise_order_usage(self, order_id: str, actor_id: str = "system") -> Dict[str, Any]:
        now = _now()
        with self._session() as conn:
            order = self.get_enterprise_order(order_id, conn=conn)
            if order["usage_event_id"]:
                return {**order, "payout": None}
            if order["status"] in {"cancelled", "closed"}:
                raise ValueError("enterprise_order_not_billable")
            dataset = self.get_dataset(order["dataset_id"], conn=conn)
            cases = [self.get_case(case_id, conn=conn) for case_id in dataset["case_ids"]]
            for case in cases:
                if not self._case_authorization_active(conn, case):
                    raise ValueError("authorization_not_active")
            usage_event_id = _id("use")
            self._execute(
                conn,
                """
                INSERT INTO usage_events
                (id, event_type, dataset_id, gross_revenue_cents, direct_cost_cents, billable, payout_eligible, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage_event_id,
                    "enterprise_order_recognized",
                    order["dataset_id"],
                    order["gross_revenue_cents"],
                    order["direct_cost_cents"],
                    1,
                    1,
                    now,
                ),
            )
            for case in cases:
                self._execute(conn, "INSERT INTO usage_event_cases (usage_event_id, case_id) VALUES (?, ?)", (usage_event_id, case["case_id"]))
            payout = calculate_payout(
                RevenueEvent(
                    event_id=usage_event_id,
                    gross_revenue_cents=order["gross_revenue_cents"],
                    direct_cost_cents=order["direct_cost_cents"],
                ),
                [_contribution_from_case(case) for case in cases],
            )
            for allocation in payout.allocations:
                self._execute(
                    conn,
                    """
                    INSERT INTO payout_events
                    (id, usage_event_id, contributor_id, case_id, amount_cents, weight, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_id("pay"), usage_event_id, allocation.contributor_id, allocation.case_id, allocation.amount_cents, allocation.weight, allocation.status, now),
                )
            self._execute(
                conn,
                """
                UPDATE enterprise_orders
                SET status = ?, usage_event_id = ?, recognized_at = ?, updated_at = ?
                WHERE id = ?
                """,
                ("recognized", usage_event_id, now, now, order_id),
            )
            self._audit(conn, actor_id, "enterprise_order.recognized", "enterprise_order", order_id, {"usage_event_id": usage_event_id})
            updated = self.get_enterprise_order(order_id, conn=conn)
            updated["payout"] = to_jsonable(payout)
            return updated

    def create_dataset_delivery_grant(
        self,
        dataset_id: str,
        customer_id: str,
        purpose: str,
        terms_version: str,
        expires_at: Optional[str] = None,
        max_reads: int = 100,
        order_id: Optional[str] = None,
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        max_reads = _bounded_limit(max_reads, 10_000)
        token = new_api_token()
        grant_id = _id("dgrant")
        now = _now()
        expires_at_value = _normalize_expires_at(expires_at) or _future_hours(self.settings.delivery_grant_ttl_hours)
        with self._session() as conn:
            dataset = self.get_dataset(dataset_id, conn=conn)
            if dataset["status"] != "ready":
                raise ValueError("dataset_not_ready")
            customer = self.get_enterprise_customer(customer_id, conn=conn)
            if customer["status"] != "active":
                raise ValueError("enterprise_customer_not_active")
            order: Optional[Dict[str, Any]] = None
            if order_id:
                order = self.get_enterprise_order(order_id, conn=conn)
                if order["dataset_id"] != dataset_id or order["customer_id"] != customer_id:
                    raise ValueError("enterprise_order_scope_mismatch")
                if order["status"] in {"cancelled", "closed"}:
                    raise ValueError("enterprise_order_not_deliverable")
                if max_reads > int(order["max_reads"]):
                    raise ValueError("delivery_reads_exceed_order")
            self._execute(
                conn,
                """
                INSERT INTO dataset_delivery_grants
                (id, order_id, dataset_id, customer_id, purpose, terms_version, status, token_hash, token_suffix,
                 expires_at, max_reads, read_count, created_by, created_at, revoked_at, last_read_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant_id,
                    order_id,
                    dataset_id,
                    customer_id,
                    purpose[:80] or dataset["purpose"],
                    terms_version[:80] or "enterprise-delivery-2026-05",
                    "active",
                    token_hash(token),
                    token_suffix(token),
                    expires_at_value,
                    max_reads,
                    0,
                    actor_id,
                    now,
                    None,
                    None,
                ),
            )
            if order:
                self._execute(
                    conn,
                    """
                    UPDATE enterprise_orders
                    SET status = CASE WHEN status = ? THEN ? ELSE status END,
                        delivery_grant_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("ready", "delivering", grant_id, now, order_id),
                )
            self._audit(conn, actor_id, "delivery_grant.created", "delivery_grant", grant_id, {"dataset_id": dataset_id, "customer_id": customer_id})
            grant = self.get_dataset_delivery_grant(grant_id, conn=conn)
            grant["delivery_token"] = token
            return grant

    def get_dataset_delivery_grant(self, grant_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_dataset_delivery_grant(grant_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM dataset_delivery_grants WHERE id = ?", (grant_id,))
        if not row:
            raise KeyError("delivery_grant_not_found")
        return self._delivery_grant_from_row(row)

    def list_dataset_delivery_grants(
        self,
        limit: int = 100,
        offset: int = 0,
        dataset_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if dataset_id:
            filters.append("dataset_id = ?")
            params.append(dataset_id)
        if customer_id:
            filters.append("customer_id = ?")
            params.append(customer_id)
        if status:
            filters.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._delivery_grant_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM dataset_delivery_grants
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def revoke_dataset_delivery_grant(self, grant_id: str, actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
            grant = self.get_dataset_delivery_grant(grant_id, conn=conn)
            if grant["status"] == "revoked":
                return grant
            now = _now()
            self._execute(
                conn,
                """
                UPDATE dataset_delivery_grants
                SET status = ?, revoked_at = ?
                WHERE id = ?
                """,
                ("revoked", now, grant_id),
            )
            self._audit(conn, actor_id, "delivery_grant.revoked", "delivery_grant", grant_id, {})
            return self.get_dataset_delivery_grant(grant_id, conn=conn)

    def read_delivery_grant_artifact(self, grant_id: str, token: str, artifact: str) -> Dict[str, Any]:
        hashed = token_hash(token)
        now = _now()
        with self._session() as conn:
            row = self._get_one(conn, "SELECT * FROM dataset_delivery_grants WHERE id = ?", (grant_id,))
            if not row:
                raise KeyError("delivery_grant_not_found")
            grant = self._delivery_grant_from_row(row)
            if grant["status"] != "active" or grant["revoked_at"]:
                raise ValueError("delivery_grant_not_active")
            if grant["expires_at"] and _is_expired(grant["expires_at"]):
                raise ValueError("delivery_grant_expired")
            if not hmac.compare_digest(hashed, row["token_hash"]):
                raise ValueError("invalid_delivery_token")
            _dataset_artifact_descriptor(artifact)
            if grant.get("order_id"):
                order = self.get_enterprise_order(grant["order_id"], conn=conn)
                if int(grant["read_count"]) >= int(order["max_reads"]):
                    raise ValueError("delivery_grant_read_limit_exceeded")
                self._assert_tenant_delivery_read_quota(conn, order["tenant_id"])
            cursor = self._execute(
                conn,
                """
                UPDATE dataset_delivery_grants
                SET read_count = read_count + 1, last_read_at = ?
                WHERE id = ? AND status = ? AND revoked_at IS NULL AND read_count < max_reads
                """,
                (now, grant_id, "active"),
            )
            if cursor.rowcount == 0:
                raise ValueError("delivery_grant_read_limit_exceeded")
            if grant.get("order_id"):
                self._execute(
                    conn,
                    "UPDATE enterprise_orders SET last_delivery_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, grant["order_id"]),
                )
            self._audit(conn, f"customer:{grant['customer_id']}", "delivery_grant.artifact_read", "delivery_grant", grant_id, {"artifact": artifact})
        return self.read_dataset_artifact(grant["dataset_id"], artifact, actor_id=f"customer:{grant['customer_id']}")

    def upsert_tenant_quota(
        self,
        tenant_id: str,
        monthly_order_limit: int = 0,
        monthly_delivery_read_limit: int = 0,
        monthly_submission_limit: int = 0,
        monthly_asset_bytes_limit: int = 0,
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        clean_tenant_id = _clean_tenant_id(tenant_id)
        now = _now()
        values = (
            max(0, int(monthly_order_limit)),
            max(0, int(monthly_delivery_read_limit)),
            max(0, int(monthly_submission_limit)),
            max(0, int(monthly_asset_bytes_limit)),
        )
        with self._session() as conn:
            self._ensure_tenant(conn, clean_tenant_id, clean_tenant_id, actor_id=actor_id)
            existing = self._get_one(conn, "SELECT tenant_id FROM tenant_quotas WHERE tenant_id = ?", (clean_tenant_id,))
            if existing:
                self._execute(
                    conn,
                    """
                    UPDATE tenant_quotas
                    SET monthly_order_limit = ?, monthly_delivery_read_limit = ?,
                        monthly_submission_limit = ?, monthly_asset_bytes_limit = ?,
                        updated_by = ?, updated_at = ?
                    WHERE tenant_id = ?
                    """,
                    (*values, actor_id, now, clean_tenant_id),
                )
                event_type = "tenant_quota.updated"
            else:
                self._execute(
                    conn,
                    """
                    INSERT INTO tenant_quotas
                    (tenant_id, monthly_order_limit, monthly_delivery_read_limit,
                     monthly_submission_limit, monthly_asset_bytes_limit,
                     updated_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (clean_tenant_id, *values, actor_id, now, now),
                )
                event_type = "tenant_quota.created"
            self._audit(conn, actor_id, event_type, "tenant_quota", clean_tenant_id, {})
            return self.get_tenant_quota(clean_tenant_id, conn=conn)

    def get_tenant_quota(self, tenant_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        clean_tenant_id = _clean_tenant_id(tenant_id)
        if conn is None:
            with self._session() as active:
                return self.get_tenant_quota(clean_tenant_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM tenant_quotas WHERE tenant_id = ?", (clean_tenant_id,))
        if not row:
            return {
                "tenant_id": clean_tenant_id,
                "monthly_order_limit": 0,
                "monthly_delivery_read_limit": 0,
                "monthly_submission_limit": 0,
                "monthly_asset_bytes_limit": 0,
                "updated_by": "",
                "created_at": "",
                "updated_at": "",
            }
        return self._tenant_quota_from_row(row)

    def list_tenant_quotas(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        with self._session() as conn:
            return [
                self._tenant_quota_from_row(row)
                for row in self._execute(
                    conn,
                    """
                    SELECT * FROM tenant_quotas
                    ORDER BY updated_at DESC, tenant_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
            ]

    def create_dispute(
        self,
        entity_type: str,
        entity_id: str,
        reason: str,
        hold_payouts: bool = True,
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        entity_type = _clean_dispute_entity_type(entity_type)
        dispute_id = _id("dsp")
        now = _now()
        with self._session() as conn:
            self._assert_dispute_entity_exists(conn, entity_type, entity_id)
            self._execute(
                conn,
                """
                INSERT INTO disputes
                (id, entity_type, entity_id, status, reason, resolution, opened_by,
                 resolved_by, hold_payouts, held_payout_count, created_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (dispute_id, entity_type, entity_id, "open", reason[:2000], "", actor_id, "", 1 if hold_payouts else 0, 0, now, None),
            )
            held_count = 0
            if hold_payouts:
                held_count = self._hold_payouts_for_dispute(conn, dispute_id, entity_type, entity_id)
                self._execute(conn, "UPDATE disputes SET held_payout_count = ? WHERE id = ?", (held_count, dispute_id))
            self._audit(conn, actor_id, "dispute.opened", "dispute", dispute_id, {"entity_type": entity_type, "entity_id": entity_id, "held_payout_count": held_count})
            return self.get_dispute(dispute_id, conn=conn)

    def get_dispute(self, dispute_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_dispute(dispute_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM disputes WHERE id = ?", (dispute_id,))
        if not row:
            raise KeyError("dispute_not_found")
        return self._dispute_from_row(row)

    def list_disputes(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if status:
            filters.append("status = ?")
            params.append(status)
        if entity_id:
            filters.append("entity_id = ?")
            params.append(entity_id)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._dispute_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM disputes
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def resolve_dispute(self, dispute_id: str, decision: str, resolution: str, actor_id: str = "system") -> Dict[str, Any]:
        decision = decision.strip().lower()
        if decision not in {"release", "void"}:
            raise ValueError("invalid_dispute_decision")
        now = _now()
        with self._session() as conn:
            dispute = self.get_dispute(dispute_id, conn=conn)
            if dispute["status"] != "open":
                raise ValueError("dispute_already_resolved")
            target_status = "pending" if decision == "release" else "voided"
            hold_rows = self._execute(conn, "SELECT * FROM dispute_holds WHERE dispute_id = ?", (dispute_id,))
            released_count = 0
            for hold in hold_rows:
                cursor = self._execute(
                    conn,
                    "UPDATE payout_events SET status = ? WHERE id = ? AND status = ?",
                    (target_status if decision == "void" else hold["previous_status"], hold["payout_id"], "held"),
                )
                released_count += cursor.rowcount
            self._execute(
                conn,
                """
                UPDATE disputes
                SET status = ?, resolution = ?, resolved_by = ?, resolved_at = ?
                WHERE id = ?
                """,
                (f"resolved_{decision}", resolution[:2000], actor_id, now, dispute_id),
            )
            self._audit(conn, actor_id, f"dispute.resolved_{decision}", "dispute", dispute_id, {"payout_count": released_count})
            return self.get_dispute(dispute_id, conn=conn)

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
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        params: List[Any] = []
        filters: List[str] = []
        if contributor_id:
            filters.append("contributor_id = ?")
            params.append(contributor_id)
        if status:
            filters.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
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

    def upsert_payout_profile(
        self,
        contributor_id: str,
        country_region: str,
        account_type: str,
        account_reference: str,
        kyc_status: str = "pending",
        tax_status: str = "pending",
        risk_status: str = "clear",
        withholding_rate_bps: int = 0,
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        now = _now()
        country_region = (country_region or "CN").strip().upper()[:40] or "CN"
        account_type = (account_type or "bank").strip().lower()[:40] or "bank"
        kyc_status = _clean_payout_status(kyc_status, {"pending", "verified", "rejected"}, "invalid_kyc_status")
        tax_status = _clean_payout_status(tax_status, {"pending", "verified", "not_required", "rejected"}, "invalid_tax_status")
        risk_status = _clean_payout_status(risk_status, {"clear", "review", "blocked", "rejected"}, "invalid_risk_status")
        status = _payout_profile_status(kyc_status, tax_status, risk_status)
        withholding_rate_bps = max(0, min(10_000, int(withholding_rate_bps)))
        account_reference = str(account_reference or "").strip()
        if len(account_reference) < 4:
            raise ValueError("account_reference_required")
        account_ref_hash = _sha256(account_reference)
        account_ref_suffix = account_reference[-4:]
        with self._session() as conn:
            existing = self._get_one(conn, "SELECT contributor_id FROM payout_profiles WHERE contributor_id = ?", (contributor_id,))
            if existing:
                self._execute(
                    conn,
                    """
                    UPDATE payout_profiles
                    SET status = ?, country_region = ?, account_type = ?, account_ref_hash = ?, account_ref_suffix = ?,
                        kyc_status = ?, tax_status = ?, risk_status = ?, withholding_rate_bps = ?,
                        updated_by = ?, updated_at = ?
                    WHERE contributor_id = ?
                    """,
                    (
                        status,
                        country_region,
                        account_type,
                        account_ref_hash,
                        account_ref_suffix,
                        kyc_status,
                        tax_status,
                        risk_status,
                        withholding_rate_bps,
                        actor_id,
                        now,
                        contributor_id,
                    ),
                )
                event_type = "payout_profile.updated"
            else:
                self._execute(
                    conn,
                    """
                    INSERT INTO payout_profiles
                    (contributor_id, status, country_region, account_type, account_ref_hash, account_ref_suffix,
                     kyc_status, tax_status, risk_status, withholding_rate_bps, updated_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contributor_id,
                        status,
                        country_region,
                        account_type,
                        account_ref_hash,
                        account_ref_suffix,
                        kyc_status,
                        tax_status,
                        risk_status,
                        withholding_rate_bps,
                        actor_id,
                        now,
                        now,
                    ),
                )
                event_type = "payout_profile.created"
            self._audit(conn, actor_id, event_type, "payout_profile", contributor_id, {"status": status, "kyc_status": kyc_status, "tax_status": tax_status})
            return self.get_payout_profile(contributor_id, conn=conn)

    def get_payout_profile(self, contributor_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_payout_profile(contributor_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM payout_profiles WHERE contributor_id = ?", (contributor_id,))
        if not row:
            raise KeyError("payout_profile_not_found")
        return self._payout_profile_from_row(row)

    def list_payout_profiles(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        params: List[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._payout_profile_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM payout_profiles
                    {where}
                    ORDER BY updated_at DESC, contributor_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def create_payout_batch(
        self,
        contributor_id: Optional[str] = None,
        min_amount_cents: int = 1,
        max_events: int = 1000,
        actor_id: str = "system",
    ) -> Dict[str, Any]:
        max_events = _bounded_limit(max_events, self.settings.max_page_limit)
        filters = ["status = ?"]
        params: List[Any] = ["pending"]
        if contributor_id:
            filters.append("contributor_id = ?")
            params.append(contributor_id)
        params.append(max_events)
        batch_id = _id("pbat")
        now = _now()
        with self._session() as conn:
            lock_clause = "FOR UPDATE SKIP LOCKED" if self.db.use_postgres else ""
            rows = [
                row_to_dict(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM payout_events
                    WHERE {' AND '.join(filters)}
                    ORDER BY created_at ASC, id ASC
                    LIMIT ?
                    {lock_clause}
                    """,
                    tuple(params),
                )
            ]
            total = sum(int(row["amount_cents"]) for row in rows)
            if not rows or total < max(1, min_amount_cents):
                raise ValueError("no_payouts_eligible")
            manifest = {
                "batch_id": batch_id,
                "contributor_id": contributor_id,
                "payout_count": len(rows),
                "total_amount_cents": total,
                "payout_ids": [row["id"] for row in rows],
                "generated_at": now,
            }
            manifest_ref = self.objects.put_text(
                f"payout_batches/{batch_id}/manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            self._execute(
                conn,
                """
                INSERT INTO payout_batches
                (id, status, contributor_id, payout_count, total_amount_cents, manifest_path,
                 created_by, created_at, settled_by, settled_at, external_reference, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (batch_id, "ready", contributor_id, len(rows), total, manifest_ref.uri, actor_id, now, None, None, "", ""),
            )
            for row in rows:
                self._execute(
                    conn,
                    """
                    UPDATE payout_events
                    SET status = ?, settlement_batch_id = ?
                    WHERE id = ? AND status = ?
                    """,
                    ("batched", batch_id, row["id"], "pending"),
                )
            self._audit(conn, actor_id, "payout_batch.created", "payout_batch", batch_id, {"payout_count": len(rows), "total_amount_cents": total})
            return self.get_payout_batch(batch_id, conn=conn)

    def settle_payout_batch(
        self,
        batch_id: str,
        actor_id: str = "system",
        external_reference: str = "",
        notes: str = "",
    ) -> Dict[str, Any]:
        with self._session() as conn:
            batch = self.get_payout_batch(batch_id, conn=conn)
            if batch["status"] != "ready":
                raise ValueError("payout_batch_not_ready")
            if self.settings.require_payout_profile_for_settlement:
                blockers = self._payout_profile_blockers(conn, batch_id)
                if blockers:
                    raise ValueError("payout_profile_not_ready")
            now = _now()
            self._execute(
                conn,
                """
                UPDATE payout_batches
                SET status = ?, settled_by = ?, settled_at = ?, external_reference = ?, notes = ?
                WHERE id = ?
                """,
                ("settled", actor_id, now, external_reference[:160], notes[:1000], batch_id),
            )
            self._execute(
                conn,
                """
                UPDATE payout_events
                SET status = ?, settled_at = ?
                WHERE settlement_batch_id = ? AND status = ?
                """,
                ("settled", now, batch_id, "batched"),
            )
            self._audit(conn, actor_id, "payout_batch.settled", "payout_batch", batch_id, {"external_reference": external_reference[:160]})
            return self.get_payout_batch(batch_id, conn=conn)

    def get_payout_batch(self, batch_id: str, conn: Optional[Any] = None) -> Dict[str, Any]:
        if conn is None:
            with self._session() as active:
                return self.get_payout_batch(batch_id, conn=active)
        row = self._get_one(conn, "SELECT * FROM payout_batches WHERE id = ?", (batch_id,))
        if not row:
            raise KeyError("payout_batch_not_found")
        return row_to_dict(row)

    def list_payout_batches(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        params: List[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                row_to_dict(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM payout_batches
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def contributor_ledger(self, contributor_id: str) -> Dict[str, Any]:
        payouts = self.list_payout_events(limit=self.settings.max_page_limit, contributor_id=contributor_id)
        with self._session() as conn:
            payout_totals = _payout_totals_by_status(conn, self, contributor_id)
        return {
            "contributor_id": contributor_id,
            "pending_cents": payout_totals["amounts"].get("pending", 0),
            "batched_cents": payout_totals["amounts"].get("batched", 0),
            "settled_cents": payout_totals["amounts"].get("settled", 0),
            "total_cents": sum(payout_totals["amounts"].values()),
            "payout_count": payout_totals["total_count"],
            "items": payouts,
        }

    def settle_payout_event(self, payout_id: str, actor_id: str = "system") -> Dict[str, Any]:
        with self._session() as conn:
            row = self._get_one(conn, "SELECT * FROM payout_events WHERE id = ?", (payout_id,))
            if not row:
                raise KeyError("payout_not_found")
            if row["status"] != "pending":
                raise ValueError("payout_not_pending")
            if self.settings.require_payout_profile_for_settlement:
                profile = self._get_one(conn, "SELECT * FROM payout_profiles WHERE contributor_id = ?", (row["contributor_id"],))
                if not profile or not _payout_profile_ready(row_to_dict(profile)):
                    raise ValueError("payout_profile_not_ready")
            self._execute(conn, "UPDATE payout_events SET status = ?, settled_at = ? WHERE id = ?", ("settled", _now(), payout_id))
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
        purged_assets: List[str] = []
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
            remaining = max(0, limit - len(purged))
            if remaining:
                asset_rows = self._execute(
                    conn,
                    """
                    SELECT id, raw_path
                    FROM assets
                    WHERE raw_deleted_at IS NULL AND raw_expires_at IS NOT NULL AND raw_expires_at <= ?
                    ORDER BY raw_expires_at ASC
                    LIMIT ?
                    """,
                    (now, remaining),
                )
                for row in asset_rows:
                    self.objects.delete(row["raw_path"])
                    self._execute(conn, "UPDATE assets SET raw_deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, row["id"]))
                    self._audit(conn, actor_id, "raw_asset.purged", "asset", row["id"], {})
                    purged_assets.append(row["id"])
        return {
            "purged_count": len(purged) + len(purged_assets),
            "submission_ids": purged,
            "asset_ids": purged_assets,
        }

    def metrics_snapshot(self) -> Dict[str, Any]:
        with self._session() as conn:
            return {
                "cases": _count_by(conn, self, "cases", "status"),
                "assets": _count_by(conn, self, "assets", "status"),
                "jobs": _count_by(conn, self, "jobs", "status"),
                "datasets": _single_count(conn, self, "datasets"),
                "enterprise_orders": _count_by(conn, self, "enterprise_orders", "status"),
                "disputes": _count_by(conn, self, "disputes", "status"),
                "users": _count_by(conn, self, "users", "status"),
                "authorizations": _count_by(conn, self, "authorization_snapshots", "status"),
                "pending_payout_cents": _sum_where(conn, self, "payout_events", "amount_cents", "status", "pending"),
                "audit_events": _single_count(conn, self, "audit_logs"),
            }

    def observability_snapshot(self) -> Dict[str, Any]:
        with self._session() as conn:
            readiness = self.readiness_check()
            return {
                "ok": readiness["ok"],
                "readiness": readiness,
                "metrics": self.metrics_snapshot(),
                "case_drl": _count_by(conn, self, "cases", "drl"),
                "payouts": _count_by(conn, self, "payout_events", "status"),
                "payout_batches": _count_by(conn, self, "payout_batches", "status"),
                "enterprise_orders": _count_by(conn, self, "enterprise_orders", "status"),
                "disputes": _count_by(conn, self, "disputes", "status"),
                "reviews": _count_by(conn, self, "reviews", "review_type"),
                "model_invocations": _count_by(conn, self, "model_invocations", "status"),
                "queue_depth": _count_grouped(conn, self, "jobs", ["queue_name", "status"]),
            }

    def list_model_invocations(
        self,
        limit: int = 100,
        offset: int = 0,
        entity_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if entity_id:
            filters.append("entity_id = ?")
            params.append(entity_id)
        if status:
            filters.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._model_invocation_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM model_invocations
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def list_vendor_processing_records(
        self,
        limit: int = 100,
        offset: int = 0,
        entity_id: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit, offset = _page_bounds(limit, offset, self.settings.max_page_limit)
        filters: List[str] = []
        params: List[Any] = []
        if entity_id:
            filters.append("entity_id = ?")
            params.append(entity_id)
        if provider:
            filters.append("provider = ?")
            params.append(provider)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        with self._session() as conn:
            return [
                self._vendor_processing_from_row(row)
                for row in self._execute(
                    conn,
                    f"""
                    SELECT * FROM vendor_processing_records
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
            ]

    def prometheus_metrics(self) -> str:
        snapshot = self.observability_snapshot()
        lines = [
            "# HELP lodia_service_ready Service readiness flag.",
            "# TYPE lodia_service_ready gauge",
            f"lodia_service_ready {1 if snapshot['ok'] else 0}",
        ]
        for status, value in snapshot["metrics"].get("cases", {}).items():
            lines.append(f'lodia_cases_total{{status="{_label_value(status)}"}} {int(value)}')
        for drl, value in snapshot["case_drl"].items():
            lines.append(f'lodia_cases_by_drl_total{{drl="{_label_value(drl)}"}} {int(value)}')
        for status, value in snapshot["metrics"].get("assets", {}).items():
            lines.append(f'lodia_assets_total{{status="{_label_value(status)}"}} {int(value)}')
        for key, value in snapshot["queue_depth"].items():
            queue_name, status = _split_metric_key(key)
            lines.append(f'lodia_jobs_total{{queue="{_label_value(queue_name)}",status="{_label_value(status)}"}} {int(value)}')
        for status, value in snapshot["payouts"].items():
            lines.append(f'lodia_payout_events_total{{status="{_label_value(status)}"}} {int(value)}')
        for status, value in snapshot["enterprise_orders"].items():
            lines.append(f'lodia_enterprise_orders_total{{status="{_label_value(status)}"}} {int(value)}')
        for status, value in snapshot["disputes"].items():
            lines.append(f'lodia_disputes_total{{status="{_label_value(status)}"}} {int(value)}')
        for status, value in snapshot["model_invocations"].items():
            lines.append(f'lodia_model_invocations_total{{status="{_label_value(status)}"}} {int(value)}')
        lines.append(f"lodia_pending_payout_cents {int(snapshot['metrics'].get('pending_payout_cents', 0))}")
        lines.append(f"lodia_audit_events_total {int(snapshot['metrics'].get('audit_events', 0))}")
        return "\n".join(lines) + "\n"

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

    def _resolve_authorization(
        self,
        conn: Any,
        owner_id: str,
        allowed_uses: List[str],
        authorization_snapshot_id: Optional[str],
        actor_id: str,
        source: str,
    ) -> Dict[str, Any]:
        if authorization_snapshot_id:
            authorization = self.get_authorization_snapshot(authorization_snapshot_id, conn=conn)
            if authorization["owner_id"] != owner_id:
                raise ValueError("authorization_owner_mismatch")
            if authorization["status"] != "active":
                raise ValueError("authorization_not_active")
            requested = set(_clean_allowed_uses(allowed_uses))
            granted = set(authorization["allowed_uses"])
            if not requested.issubset(granted):
                raise ValueError("authorization_scope_exceeded")
            return authorization
        return self.create_authorization_snapshot(
            owner_id=owner_id,
            allowed_uses=allowed_uses,
            source=source,
            actor_id=actor_id,
            conn=conn,
        )

    def _ensure_tenant(self, conn: Any, tenant_id: str, name: str, actor_id: str = "system") -> None:
        if self._get_one(conn, "SELECT id FROM tenants WHERE id = ?", (tenant_id,)):
            return
        now = _now()
        self._execute(
            conn,
            """
            INSERT INTO tenants (id, name, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tenant_id, name[:160] or tenant_id, "active", now, now),
        )
        self._audit(conn, actor_id, "tenant.created", "tenant", tenant_id, {"auto_created": True})

    def _create_submission_from_asset(
        self,
        conn: Any,
        asset_id: str,
        owner_id: str,
        text: str,
        allowed_uses: List[str],
        authorization_snapshot_id: str,
        actor_id: str,
    ) -> str:
        submission_id = _id("sub")
        raw_ref = self.objects.put_text(f"raw/{submission_id}.txt", text)
        now = _now()
        self._execute(
            conn,
            """
            INSERT INTO submissions
            (id, owner_id, source_type, status, raw_path, raw_hash, allowed_uses_json,
             authorization_snapshot_id, raw_expires_at, raw_deleted_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submission_id,
                owner_id,
                "asset_text",
                "quarantined",
                raw_ref.uri,
                _sha256(text),
                dumps(_clean_allowed_uses(allowed_uses)),
                authorization_snapshot_id,
                _future_hours(self.settings.raw_object_ttl_hours),
                None,
                now,
            ),
        )
        self._audit(
            conn,
            actor_id,
            "submission.created_from_asset",
            "submission",
            submission_id,
            {"asset_id": asset_id, "authorization_snapshot_id": authorization_snapshot_id},
        )
        return submission_id

    def _case_authorization_active(self, conn: Any, case: Dict[str, Any]) -> bool:
        snapshot_id = case.get("authorization_snapshot_id")
        if not snapshot_id:
            return False
        row = self._get_one(conn, "SELECT status FROM authorization_snapshots WHERE id = ?", (snapshot_id,))
        return bool(row and row["status"] == "active")

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

    def _delete_object_quietly(self, uri: str) -> None:
        try:
            self.objects.delete(uri)
        except Exception:
            return None

    def _record_model_invocation(self, conn: Any, invocation: Dict[str, Any]) -> None:
        invocation_id = _id("minv")
        now = _now()
        self._execute(
            conn,
            """
            INSERT INTO model_invocations
            (id, provider, task_type, entity_type, entity_id, status, input_hash, output_json,
             error, cost_micros, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invocation_id,
                invocation["provider"],
                invocation["task_type"],
                invocation["entity_type"],
                invocation["entity_id"],
                invocation["status"],
                invocation.get("input_hash", ""),
                dumps(invocation.get("output", {})),
                invocation.get("error", ""),
                int(invocation.get("cost_micros", 0)),
                int(invocation.get("latency_ms", 0)),
                now,
            ),
        )
        self._execute(
            conn,
            """
            INSERT INTO vendor_processing_records
            (id, model_invocation_id, provider, service_type, entity_type, entity_id,
             data_category, status, region, purpose, input_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _id("vpr"),
                invocation_id,
                invocation["provider"],
                invocation["task_type"],
                invocation["entity_type"],
                invocation["entity_id"],
                _vendor_data_category(invocation["task_type"]),
                invocation["status"],
                self.settings.region,
                _vendor_processing_purpose(invocation["task_type"]),
                invocation.get("input_hash", ""),
                now,
            ),
        )

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
            "authorization_snapshot_id": row["authorization_snapshot_id"] if "authorization_snapshot_id" in row.keys() else None,
            "review_claimed_by": row["review_claimed_by"] if "review_claimed_by" in row.keys() else None,
            "review_claimed_at": row["review_claimed_at"] if "review_claimed_at" in row.keys() else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _user_from_row(self, row: Any) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"] if "tenant_id" in row.keys() else "default",
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

    def _authorization_from_row(self, row: Any) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "status": row["status"],
            "allowed_uses": loads(row["allowed_uses_json"]),
            "policy_version": row["policy_version"],
            "terms_version": row["terms_version"],
            "consent_text_hash": row["consent_text_hash"],
            "source": row["source"],
            "created_at": row["created_at"],
            "withdrawn_at": row["withdrawn_at"],
            "withdrawal_reason": row["withdrawal_reason"],
        }

    def _asset_from_row(self, row: Any) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "submission_id": row["submission_id"],
            "authorization_snapshot_id": row["authorization_snapshot_id"],
            "filename": row["filename"],
            "media_type": row["media_type"],
            "asset_type": row["asset_type"],
            "byte_size": row["byte_size"],
            "sha256": row["sha256"],
            "status": row["status"],
            "raw_path": row["raw_path"],
            "extracted_text_path": row["extracted_text_path"],
            "metadata": loads(row["metadata_json"]),
            "risk": loads(row["risk_json"]),
            "redaction": loads(row["redaction_json"]) if row["redaction_json"] else {},
            "raw_expires_at": row["raw_expires_at"],
            "raw_deleted_at": row["raw_deleted_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _asset_upload_session_from_row(self, row: Any) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "asset_id": row["asset_id"],
            "owner_id": row["owner_id"],
            "authorization_snapshot_id": row["authorization_snapshot_id"],
            "filename": row["filename"],
            "media_type": row["media_type"],
            "expected_byte_size": row["expected_byte_size"],
            "object_key": row["object_key"],
            "object_uri": row["object_uri"],
            "status": row["status"],
            "allowed_uses": loads(row["allowed_uses_json"]),
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
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

    def _review_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result["rubric"] = loads(result.pop("rubric_json") or "{}")
        result["evidence"] = loads(result.pop("evidence_json") or "{}")
        return result

    def _model_invocation_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result["output"] = loads(result.pop("output_json") or "{}")
        return result

    def _vendor_processing_from_row(self, row: Any) -> Dict[str, Any]:
        return row_to_dict(row)

    def _enterprise_customer_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result.pop("contact_email_hash", None)
        return result

    def _enterprise_contract_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result["terms"] = loads(result.pop("terms_json") or "{}")
        return result

    def _enterprise_order_from_row(self, row: Any) -> Dict[str, Any]:
        return row_to_dict(row)

    def _delivery_grant_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result.pop("token_hash", None)
        return result

    def _payout_profile_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result.pop("account_ref_hash", None)
        return result

    def _tenant_quota_from_row(self, row: Any) -> Dict[str, Any]:
        return row_to_dict(row)

    def _dispute_from_row(self, row: Any) -> Dict[str, Any]:
        result = row_to_dict(row)
        result["hold_payouts"] = bool(result["hold_payouts"])
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

    def _payout_profile_blockers(self, conn: Any, batch_id: str) -> List[str]:
        rows = self._execute(
            conn,
            """
            SELECT DISTINCT p.contributor_id
            FROM payout_events p
            LEFT JOIN payout_profiles profile ON profile.contributor_id = p.contributor_id
            WHERE p.settlement_batch_id = ? AND p.status = ?
              AND (
                profile.contributor_id IS NULL
                OR profile.status != ?
                OR profile.kyc_status != ?
                OR profile.tax_status NOT IN (?, ?)
                OR profile.risk_status != ?
              )
            """,
            (batch_id, "batched", "active", "verified", "verified", "not_required", "clear"),
        )
        return [row["contributor_id"] for row in rows]

    def _assert_tenant_order_quota(self, conn: Any, tenant_id: str) -> None:
        quota = self.get_tenant_quota(tenant_id, conn=conn)
        limit = int(quota.get("monthly_order_limit") or 0)
        if limit <= 0:
            return
        row = self._get_one(
            conn,
            """
            SELECT COUNT(*) AS value
            FROM enterprise_orders
            WHERE tenant_id = ? AND created_at >= ?
            """,
            (tenant_id, _month_start()),
        )
        if row and int(row["value"]) >= limit:
            raise ValueError("tenant_order_quota_exceeded")

    def _assert_tenant_delivery_read_quota(self, conn: Any, tenant_id: str) -> None:
        quota = self.get_tenant_quota(tenant_id, conn=conn)
        limit = int(quota.get("monthly_delivery_read_limit") or 0)
        if limit <= 0:
            return
        row = self._get_one(
            conn,
            """
            SELECT COALESCE(SUM(g.read_count), 0) AS value
            FROM dataset_delivery_grants g
            JOIN enterprise_customers c ON c.id = g.customer_id
            WHERE c.tenant_id = ? AND g.created_at >= ?
            """,
            (tenant_id, _month_start()),
        )
        if row and int(row["value"]) >= limit:
            raise ValueError("tenant_delivery_read_quota_exceeded")

    def _assert_dispute_entity_exists(self, conn: Any, entity_type: str, entity_id: str) -> None:
        if entity_type == "enterprise_order":
            self.get_enterprise_order(entity_id, conn=conn)
        elif entity_type == "dataset":
            self.get_dataset(entity_id, conn=conn)
        elif entity_type == "usage_event":
            if not self._get_one(conn, "SELECT id FROM usage_events WHERE id = ?", (entity_id,)):
                raise KeyError("usage_event_not_found")
        elif entity_type == "payout_event":
            if not self._get_one(conn, "SELECT id FROM payout_events WHERE id = ?", (entity_id,)):
                raise KeyError("payout_not_found")
        elif entity_type == "case":
            self.get_case(entity_id, conn=conn)
        elif entity_type == "delivery_grant":
            self.get_dataset_delivery_grant(entity_id, conn=conn)
        else:
            raise ValueError("invalid_dispute_entity_type")

    def _hold_payouts_for_dispute(self, conn: Any, dispute_id: str, entity_type: str, entity_id: str) -> int:
        rows = self._payout_rows_for_dispute_entity(conn, entity_type, entity_id)
        held = 0
        for row in rows:
            cursor = self._execute(conn, "UPDATE payout_events SET status = ? WHERE id = ? AND status = ?", ("held", row["id"], "pending"))
            if cursor.rowcount:
                self._execute(
                    conn,
                    "INSERT INTO dispute_holds (dispute_id, payout_id, previous_status, created_at) VALUES (?, ?, ?, ?)",
                    (dispute_id, row["id"], row["status"], _now()),
                )
                held += 1
        return held

    def _payout_rows_for_dispute_entity(self, conn: Any, entity_type: str, entity_id: str) -> List[Dict[str, Any]]:
        if entity_type == "payout_event":
            row = self._get_one(conn, "SELECT * FROM payout_events WHERE id = ? AND status = ?", (entity_id, "pending"))
            return [row_to_dict(row)] if row else []
        if entity_type == "case":
            rows = self._execute(conn, "SELECT * FROM payout_events WHERE case_id = ? AND status = ?", (entity_id, "pending"))
            return [row_to_dict(row) for row in rows]

        usage_event_ids: List[str] = []
        if entity_type == "enterprise_order":
            order = self.get_enterprise_order(entity_id, conn=conn)
            if order.get("usage_event_id"):
                usage_event_ids.append(order["usage_event_id"])
        elif entity_type == "usage_event":
            usage_event_ids.append(entity_id)
        elif entity_type == "dataset":
            usage_event_ids.extend(row["id"] for row in self._execute(conn, "SELECT id FROM usage_events WHERE dataset_id = ?", (entity_id,)))
        elif entity_type == "delivery_grant":
            grant = self.get_dataset_delivery_grant(entity_id, conn=conn)
            if grant.get("order_id"):
                order = self.get_enterprise_order(grant["order_id"], conn=conn)
                if order.get("usage_event_id"):
                    usage_event_ids.append(order["usage_event_id"])
            else:
                usage_event_ids.extend(row["id"] for row in self._execute(conn, "SELECT id FROM usage_events WHERE dataset_id = ?", (grant["dataset_id"],)))
        if not usage_event_ids:
            return []
        placeholders = ",".join("?" for _ in usage_event_ids)
        rows = self._execute(
            conn,
            f"SELECT * FROM payout_events WHERE status = ? AND usage_event_id IN ({placeholders})",
            ("pending", *usage_event_ids),
        )
        return [row_to_dict(row) for row in rows]

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
        self._ensure_tenant_tables(conn)
        self._ensure_authorization_tables(conn)
        self._ensure_asset_tables(conn)
        self._ensure_asset_upload_session_tables(conn)
        self._ensure_submission_columns(conn)
        self._ensure_dataset_columns(conn)
        self._ensure_case_query_columns(conn)
        self._ensure_review_columns(conn)
        self._ensure_payout_tables(conn)
        self._ensure_payout_profile_tables(conn)
        self._ensure_enterprise_delivery_tables(conn)
        self._ensure_commercial_ops_tables(conn)
        self._ensure_model_invocation_tables(conn)
        self._ensure_vendor_processing_tables(conn)
        self._backfill_authorization_snapshots(conn)
        self._execute(
            conn,
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            ("20260506_p0_foundation", _now()),
        )
        self._execute(
            conn,
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            ("20260506_p1_assets_authorization", _now()),
        )
        self._execute(
            conn,
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            ("20260506_p2_commercial_controls", _now()),
        )
        self._execute(
            conn,
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            ("20260506_p3_upload_observability", _now()),
        )
        self._execute(
            conn,
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            ("20260506_p4_contributor_review_delivery", _now()),
        )
        self._execute(
            conn,
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            ("20260506_p5_enterprise_delivery_payout_profiles", _now()),
        )
        self._execute(
            conn,
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            ("20260506_p6_commercial_ops", _now()),
        )

    def _ensure_submission_columns(self, conn: Any) -> None:
        columns = self.db.column_names(conn, "submissions")
        if "authorization_snapshot_id" not in columns:
            self._execute(conn, "ALTER TABLE submissions ADD COLUMN authorization_snapshot_id TEXT")
        if "raw_expires_at" not in columns:
            self._execute(conn, "ALTER TABLE submissions ADD COLUMN raw_expires_at TEXT")
        if "raw_deleted_at" not in columns:
            self._execute(conn, "ALTER TABLE submissions ADD COLUMN raw_deleted_at TEXT")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_submissions_raw_expiry ON submissions(raw_expires_at, raw_deleted_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_submissions_authorization ON submissions(authorization_snapshot_id)")

    def _ensure_tenant_tables(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_tenants_status_created ON tenants(status, created_at)")
        columns = self.db.column_names(conn, "users")
        if "tenant_id" not in columns:
            self._execute(conn, "ALTER TABLE users ADD COLUMN tenant_id TEXT")
        self._execute(conn, "UPDATE users SET tenant_id = ? WHERE tenant_id IS NULL", ("default",))
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_users_tenant_status ON users(tenant_id, status, created_at)")
        self._ensure_tenant(conn, "default", "Default Tenant")
        self._ensure_tenant(conn, "platform", "Platform")

    def _ensure_dataset_columns(self, conn: Any) -> None:
        columns = self.db.column_names(conn, "datasets")
        if "data_contract_path" not in columns:
            self._execute(conn, "ALTER TABLE datasets ADD COLUMN data_contract_path TEXT")
        if "contract_status" not in columns:
            self._execute(conn, "ALTER TABLE datasets ADD COLUMN contract_status TEXT")

    def _ensure_case_query_columns(self, conn: Any) -> None:
        columns = self.db.column_names(conn, "cases")
        if "authorization_snapshot_id" not in columns:
            self._execute(conn, "ALTER TABLE cases ADD COLUMN authorization_snapshot_id TEXT")
        if "drl" not in columns:
            self._execute(conn, "ALTER TABLE cases ADD COLUMN drl TEXT")
        if "quality_score" not in columns:
            self._execute(conn, "ALTER TABLE cases ADD COLUMN quality_score REAL")
        if "review_claimed_by" not in columns:
            self._execute(conn, "ALTER TABLE cases ADD COLUMN review_claimed_by TEXT")
        if "review_claimed_at" not in columns:
            self._execute(conn, "ALTER TABLE cases ADD COLUMN review_claimed_at TEXT")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_cases_drl_quality ON cases(drl, quality_score)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_cases_authorization ON cases(authorization_snapshot_id)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_cases_review_claim ON cases(status, review_claimed_by, quality_score, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_cases_owner_status_created ON cases(owner_id, status, created_at)")

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

    def _ensure_review_columns(self, conn: Any) -> None:
        columns = self.db.column_names(conn, "reviews")
        if "review_type" not in columns:
            self._execute(conn, "ALTER TABLE reviews ADD COLUMN review_type TEXT")
        if "score" not in columns:
            self._execute(conn, "ALTER TABLE reviews ADD COLUMN score REAL")
        if "rubric_json" not in columns:
            self._execute(conn, "ALTER TABLE reviews ADD COLUMN rubric_json TEXT")
        if "evidence_json" not in columns:
            self._execute(conn, "ALTER TABLE reviews ADD COLUMN evidence_json TEXT")
        self._execute(conn, "UPDATE reviews SET review_type = ? WHERE review_type IS NULL", ("human",))
        self._execute(conn, "UPDATE reviews SET score = ? WHERE score IS NULL", (1.0,))
        self._execute(conn, "UPDATE reviews SET rubric_json = ? WHERE rubric_json IS NULL", (dumps({}),))
        self._execute(conn, "UPDATE reviews SET evidence_json = ? WHERE evidence_json IS NULL", (dumps({}),))
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_reviews_case_type ON reviews(case_id, review_type, decision)")

    def _ensure_payout_tables(self, conn: Any) -> None:
        columns = self.db.column_names(conn, "payout_events")
        if "settlement_batch_id" not in columns:
            self._execute(conn, "ALTER TABLE payout_events ADD COLUMN settlement_batch_id TEXT")
        if "settled_at" not in columns:
            self._execute(conn, "ALTER TABLE payout_events ADD COLUMN settled_at TEXT")
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS payout_batches (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                contributor_id TEXT,
                payout_count INTEGER NOT NULL,
                total_amount_cents INTEGER NOT NULL,
                manifest_path TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                settled_by TEXT,
                settled_at TEXT,
                external_reference TEXT NOT NULL,
                notes TEXT NOT NULL
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_payout_events_status_created ON payout_events(status, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_payout_events_batch ON payout_events(settlement_batch_id)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_payout_events_contributor_status ON payout_events(contributor_id, status)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_payout_batches_status_created ON payout_batches(status, created_at)")

    def _ensure_payout_profile_tables(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS payout_profiles (
                contributor_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                country_region TEXT NOT NULL,
                account_type TEXT NOT NULL,
                account_ref_hash TEXT NOT NULL,
                account_ref_suffix TEXT NOT NULL,
                kyc_status TEXT NOT NULL,
                tax_status TEXT NOT NULL,
                risk_status TEXT NOT NULL,
                withholding_rate_bps INTEGER NOT NULL,
                updated_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_payout_profiles_status_updated ON payout_profiles(status, updated_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_payout_profiles_kyc_tax ON payout_profiles(kyc_status, tax_status, risk_status)")

    def _ensure_enterprise_delivery_tables(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS enterprise_customers (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                contact_email_hash TEXT NOT NULL,
                contact_email_domain TEXT NOT NULL,
                status TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS dataset_delivery_grants (
                id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                purpose TEXT NOT NULL,
                terms_version TEXT NOT NULL,
                status TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                token_suffix TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                max_reads INTEGER NOT NULL,
                read_count INTEGER NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                last_read_at TEXT,
                FOREIGN KEY (dataset_id) REFERENCES datasets(id),
                FOREIGN KEY (customer_id) REFERENCES enterprise_customers(id)
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_enterprise_customers_status_created ON enterprise_customers(status, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_enterprise_customers_tenant ON enterprise_customers(tenant_id, status)")
        grant_columns = self.db.column_names(conn, "dataset_delivery_grants")
        if "order_id" not in grant_columns:
            self._execute(conn, "ALTER TABLE dataset_delivery_grants ADD COLUMN order_id TEXT")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_delivery_grants_dataset ON dataset_delivery_grants(dataset_id, status, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_delivery_grants_customer ON dataset_delivery_grants(customer_id, status, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_delivery_grants_order ON dataset_delivery_grants(order_id, status)")

    def _ensure_commercial_ops_tables(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS enterprise_contracts (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL,
                terms_json TEXT NOT NULL,
                effective_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                signed_by TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES enterprise_customers(id)
            )
            """,
        )
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS enterprise_orders (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                status TEXT NOT NULL,
                gross_revenue_cents INTEGER NOT NULL,
                direct_cost_cents INTEGER NOT NULL,
                currency TEXT NOT NULL,
                max_reads INTEGER NOT NULL,
                usage_event_id TEXT NOT NULL,
                delivery_grant_id TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                recognized_at TEXT,
                last_delivery_at TEXT,
                FOREIGN KEY (customer_id) REFERENCES enterprise_customers(id),
                FOREIGN KEY (dataset_id) REFERENCES datasets(id),
                FOREIGN KEY (contract_id) REFERENCES enterprise_contracts(id)
            )
            """,
        )
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS tenant_quotas (
                tenant_id TEXT PRIMARY KEY,
                monthly_order_limit INTEGER NOT NULL,
                monthly_delivery_read_limit INTEGER NOT NULL,
                monthly_submission_limit INTEGER NOT NULL,
                monthly_asset_bytes_limit INTEGER NOT NULL,
                updated_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS disputes (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                resolution TEXT NOT NULL,
                opened_by TEXT NOT NULL,
                resolved_by TEXT NOT NULL,
                hold_payouts INTEGER NOT NULL,
                held_payout_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """,
        )
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS dispute_holds (
                dispute_id TEXT NOT NULL,
                payout_id TEXT NOT NULL,
                previous_status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (dispute_id, payout_id),
                FOREIGN KEY (dispute_id) REFERENCES disputes(id),
                FOREIGN KEY (payout_id) REFERENCES payout_events(id)
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_enterprise_contracts_customer_status ON enterprise_contracts(customer_id, status, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_enterprise_orders_customer_status ON enterprise_orders(customer_id, status, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_enterprise_orders_tenant_created ON enterprise_orders(tenant_id, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_disputes_status_created ON disputes(status, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_disputes_entity ON disputes(entity_type, entity_id, status)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_dispute_holds_payout ON dispute_holds(payout_id)")

    def _ensure_model_invocation_tables(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS model_invocations (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                task_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                status TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                output_json TEXT NOT NULL,
                error TEXT NOT NULL,
                cost_micros INTEGER NOT NULL,
                latency_ms INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_model_invocations_entity ON model_invocations(entity_type, entity_id)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_model_invocations_status ON model_invocations(status, created_at)")

    def _ensure_vendor_processing_tables(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS vendor_processing_records (
                id TEXT PRIMARY KEY,
                model_invocation_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                service_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                data_category TEXT NOT NULL,
                status TEXT NOT NULL,
                region TEXT NOT NULL,
                purpose TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_vendor_processing_entity ON vendor_processing_records(entity_type, entity_id)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_vendor_processing_provider ON vendor_processing_records(provider, created_at)")

    def _ensure_authorization_tables(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS authorization_snapshots (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                status TEXT NOT NULL,
                allowed_uses_json TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                terms_version TEXT NOT NULL,
                consent_text_hash TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                withdrawn_at TEXT,
                withdrawal_reason TEXT NOT NULL
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_authorization_owner_status ON authorization_snapshots(owner_id, status, created_at)")

    def _ensure_asset_tables(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                submission_id TEXT,
                authorization_snapshot_id TEXT,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                raw_path TEXT NOT NULL,
                extracted_text_path TEXT,
                metadata_json TEXT NOT NULL,
                risk_json TEXT NOT NULL,
                redaction_json TEXT NOT NULL,
                raw_expires_at TEXT,
                raw_deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_assets_owner_status ON assets(owner_id, status, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_assets_sha256 ON assets(sha256)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_assets_submission ON assets(submission_id)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_assets_authorization ON assets(authorization_snapshot_id)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_assets_raw_expiry ON assets(raw_expires_at, raw_deleted_at)")

    def _ensure_asset_upload_session_tables(self, conn: Any) -> None:
        self._execute(
            conn,
            """
            CREATE TABLE IF NOT EXISTS asset_upload_sessions (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL UNIQUE,
                owner_id TEXT NOT NULL,
                authorization_snapshot_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                expected_byte_size INTEGER NOT NULL,
                object_key TEXT NOT NULL,
                object_uri TEXT NOT NULL,
                status TEXT NOT NULL,
                allowed_uses_json TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
            """,
        )
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_asset_upload_sessions_owner_status ON asset_upload_sessions(owner_id, status, created_at)")
        self._execute(conn, "CREATE INDEX IF NOT EXISTS idx_asset_upload_sessions_expiry ON asset_upload_sessions(status, expires_at)")

    def _backfill_authorization_snapshots(self, conn: Any) -> None:
        rows = self._execute(
            conn,
            """
            SELECT id, owner_id, allowed_uses_json
            FROM submissions
            WHERE authorization_snapshot_id IS NULL
            """,
        )
        for row in rows:
            snapshot = self.create_authorization_snapshot(
                owner_id=row["owner_id"],
                allowed_uses=loads(row["allowed_uses_json"]),
                source="migration_backfill",
                actor_id="migration",
                conn=conn,
            )
            self._execute(
                conn,
                "UPDATE submissions SET authorization_snapshot_id = ? WHERE id = ?",
                (snapshot["id"], row["id"]),
            )
            self._execute(
                conn,
                "UPDATE cases SET authorization_snapshot_id = ? WHERE submission_id = ? AND authorization_snapshot_id IS NULL",
                (snapshot["id"], row["id"]),
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
    authorization_snapshot_id TEXT,
    raw_expires_at TEXT,
    raw_deleted_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_raw_expiry ON submissions(raw_expires_at, raw_deleted_at);
CREATE INDEX IF NOT EXISTS idx_submissions_authorization ON submissions(authorization_snapshot_id);

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
    review_claimed_by TEXT,
    review_claimed_at TEXT,
    redaction_json TEXT NOT NULL,
    annotation_json TEXT NOT NULL,
    dedup_json TEXT NOT NULL,
    quality_gate_json TEXT NOT NULL,
    authorization_snapshot_id TEXT,
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
CREATE INDEX IF NOT EXISTS idx_cases_authorization ON cases(authorization_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_cases_review_claim ON cases(status, review_claimed_by, quality_score, created_at);
CREATE INDEX IF NOT EXISTS idx_cases_owner_status_created ON cases(owner_id, status, created_at);

CREATE TABLE IF NOT EXISTS authorization_snapshots (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    allowed_uses_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    terms_version TEXT NOT NULL,
    consent_text_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    withdrawn_at TEXT,
    withdrawal_reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_authorization_owner_status ON authorization_snapshots(owner_id, status, created_at);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    submission_id TEXT,
    authorization_snapshot_id TEXT,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    extracted_text_path TEXT,
    metadata_json TEXT NOT NULL,
    risk_json TEXT NOT NULL,
    redaction_json TEXT NOT NULL,
    raw_expires_at TEXT,
    raw_deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (submission_id) REFERENCES submissions(id),
    FOREIGN KEY (authorization_snapshot_id) REFERENCES authorization_snapshots(id)
);
CREATE INDEX IF NOT EXISTS idx_assets_owner_status ON assets(owner_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_assets_sha256 ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_assets_submission ON assets(submission_id);
CREATE INDEX IF NOT EXISTS idx_assets_authorization ON assets(authorization_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_assets_raw_expiry ON assets(raw_expires_at, raw_deleted_at);

CREATE TABLE IF NOT EXISTS asset_upload_sessions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    authorization_snapshot_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    expected_byte_size INTEGER NOT NULL,
    object_key TEXT NOT NULL,
    object_uri TEXT NOT NULL,
    status TEXT NOT NULL,
    allowed_uses_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_asset_upload_sessions_owner_status ON asset_upload_sessions(owner_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_asset_upload_sessions_expiry ON asset_upload_sessions(status, expires_at);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    review_type TEXT NOT NULL,
    decision TEXT NOT NULL,
    score REAL NOT NULL,
    notes TEXT NOT NULL,
    rubric_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_case_type ON reviews(case_id, review_type, decision);

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
    settlement_batch_id TEXT,
    settled_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (usage_event_id) REFERENCES usage_events(id),
    FOREIGN KEY (case_id) REFERENCES cases(id)
);
CREATE INDEX IF NOT EXISTS idx_payout_events_contributor_created ON payout_events(contributor_id, created_at);
CREATE INDEX IF NOT EXISTS idx_payout_events_usage ON payout_events(usage_event_id);
CREATE INDEX IF NOT EXISTS idx_payout_events_status_created ON payout_events(status, created_at);
CREATE INDEX IF NOT EXISTS idx_payout_events_batch ON payout_events(settlement_batch_id);
CREATE INDEX IF NOT EXISTS idx_payout_events_contributor_status ON payout_events(contributor_id, status);

CREATE TABLE IF NOT EXISTS payout_batches (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    contributor_id TEXT,
    payout_count INTEGER NOT NULL,
    total_amount_cents INTEGER NOT NULL,
    manifest_path TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    settled_by TEXT,
    settled_at TEXT,
    external_reference TEXT NOT NULL,
    notes TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payout_batches_status_created ON payout_batches(status, created_at);

CREATE TABLE IF NOT EXISTS payout_profiles (
    contributor_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    country_region TEXT NOT NULL,
    account_type TEXT NOT NULL,
    account_ref_hash TEXT NOT NULL,
    account_ref_suffix TEXT NOT NULL,
    kyc_status TEXT NOT NULL,
    tax_status TEXT NOT NULL,
    risk_status TEXT NOT NULL,
    withholding_rate_bps INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payout_profiles_status_updated ON payout_profiles(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_payout_profiles_kyc_tax ON payout_profiles(kyc_status, tax_status, risk_status);

CREATE TABLE IF NOT EXISTS enterprise_customers (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    contact_email_hash TEXT NOT NULL,
    contact_email_domain TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_enterprise_customers_status_created ON enterprise_customers(status, created_at);
CREATE INDEX IF NOT EXISTS idx_enterprise_customers_tenant ON enterprise_customers(tenant_id, status);

CREATE TABLE IF NOT EXISTS dataset_delivery_grants (
    id TEXT PRIMARY KEY,
    order_id TEXT,
    dataset_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    terms_version TEXT NOT NULL,
    status TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    token_suffix TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    max_reads INTEGER NOT NULL,
    read_count INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    last_read_at TEXT,
    FOREIGN KEY (dataset_id) REFERENCES datasets(id),
    FOREIGN KEY (customer_id) REFERENCES enterprise_customers(id)
);
CREATE INDEX IF NOT EXISTS idx_delivery_grants_dataset ON dataset_delivery_grants(dataset_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_delivery_grants_customer ON dataset_delivery_grants(customer_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_delivery_grants_order ON dataset_delivery_grants(order_id, status);

CREATE TABLE IF NOT EXISTS enterprise_contracts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    terms_json TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    signed_by TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES enterprise_customers(id)
);
CREATE INDEX IF NOT EXISTS idx_enterprise_contracts_customer_status ON enterprise_contracts(customer_id, status, created_at);

CREATE TABLE IF NOT EXISTS enterprise_orders (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    status TEXT NOT NULL,
    gross_revenue_cents INTEGER NOT NULL,
    direct_cost_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    max_reads INTEGER NOT NULL,
    usage_event_id TEXT NOT NULL,
    delivery_grant_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    recognized_at TEXT,
    last_delivery_at TEXT,
    FOREIGN KEY (customer_id) REFERENCES enterprise_customers(id),
    FOREIGN KEY (dataset_id) REFERENCES datasets(id),
    FOREIGN KEY (contract_id) REFERENCES enterprise_contracts(id)
);
CREATE INDEX IF NOT EXISTS idx_enterprise_orders_customer_status ON enterprise_orders(customer_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_enterprise_orders_tenant_created ON enterprise_orders(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS tenant_quotas (
    tenant_id TEXT PRIMARY KEY,
    monthly_order_limit INTEGER NOT NULL,
    monthly_delivery_read_limit INTEGER NOT NULL,
    monthly_submission_limit INTEGER NOT NULL,
    monthly_asset_bytes_limit INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disputes (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    resolution TEXT NOT NULL,
    opened_by TEXT NOT NULL,
    resolved_by TEXT NOT NULL,
    hold_payouts INTEGER NOT NULL,
    held_payout_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_disputes_status_created ON disputes(status, created_at);
CREATE INDEX IF NOT EXISTS idx_disputes_entity ON disputes(entity_type, entity_id, status);

CREATE TABLE IF NOT EXISTS dispute_holds (
    dispute_id TEXT NOT NULL,
    payout_id TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (dispute_id, payout_id),
    FOREIGN KEY (dispute_id) REFERENCES disputes(id),
    FOREIGN KEY (payout_id) REFERENCES payout_events(id)
);
CREATE INDEX IF NOT EXISTS idx_dispute_holds_payout ON dispute_holds(payout_id);

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tenants_status_created ON tenants(status, created_at);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_users_tenant_status ON users(tenant_id, status, created_at);

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

CREATE TABLE IF NOT EXISTS model_invocations (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    task_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_json TEXT NOT NULL,
    error TEXT NOT NULL,
    cost_micros INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_invocations_entity ON model_invocations(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_model_invocations_status ON model_invocations(status, created_at);

CREATE TABLE IF NOT EXISTS vendor_processing_records (
    id TEXT PRIMARY KEY,
    model_invocation_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    service_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    data_category TEXT NOT NULL,
    status TEXT NOT NULL,
    region TEXT NOT NULL,
    purpose TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vendor_processing_entity ON vendor_processing_records(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_vendor_processing_provider ON vendor_processing_records(provider, created_at);

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
        "expert_review_required_for_drl4_plus": True,
        "double_review_required_for_drl5": True,
    }


def _data_contract(dataset_id: str, name: str, purpose: str, min_drl: str, cases: List[Dict[str, Any]], now: str) -> Dict[str, Any]:
    return {
        "contract_id": _id("dc"),
        "version": "2026-05-06.p2",
        "dataset_id": dataset_id,
        "name": name,
        "purpose": purpose,
        "min_drl": min_drl,
        "case_count": len(cases),
        "case_ids": [case["case_id"] for case in cases],
        "authorization_snapshot_ids": sorted({case.get("authorization_snapshot_id") for case in cases if case.get("authorization_snapshot_id")}),
        "rules": {
            "contains_raw_data": False,
            "requires_redaction_passed": True,
            "requires_purpose_authorized": True,
            "requires_active_authorization_snapshot": True,
            "requires_no_required_actions": True,
            "requires_human_review_for_drl3_plus": True,
            "requires_expert_review_for_drl4_plus": True,
            "requires_double_review_for_gold_eval": True,
        },
        "generated_at": now,
    }


def _data_contract_violations(contract: Dict[str, Any], cases: List[Dict[str, Any]]) -> List[str]:
    violations: List[str] = []
    min_drl = contract["min_drl"]
    purpose = contract["purpose"]
    for case in cases:
        if case["status"] != "commercial_ready":
            violations.append("case_not_commercial_ready")
        if not case["redaction"]["passed"]:
            violations.append("redaction_not_passed")
        if DRL_ORDER.get(case["quality_gate"]["drl"], 0) < DRL_ORDER[min_drl]:
            violations.append("drl_below_minimum")
        if not _case_allowed_for_purpose(case, purpose):
            violations.append("purpose_not_authorized")
        if not case.get("authorization_snapshot_id"):
            violations.append("authorization_snapshot_missing")
        if purpose == "gold_eval" and case["quality_gate"]["drl"] != "DRL5":
            violations.append("gold_eval_requires_drl5")
        required_actions = list(case["quality_gate"].get("required_actions") or [])
        if purpose != "gold_eval":
            required_actions = [action for action in required_actions if action != "gold_second_review"]
        if required_actions:
            violations.append("required_actions_open")
    return sorted(set(violations))


def _export_record(case: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "contributor_ref": _sha256(case["owner_id"])[:16],
        "drl": case["quality_gate"]["drl"],
        "redacted_turns": [{"role": "mixed", "content": case["redacted_text"]}],
        "annotation": case["annotation"],
        "license": {
            "allowed_uses": case["quality_gate"]["allowed_uses"],
            "authorization_snapshot_id": case.get("authorization_snapshot_id"),
        },
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


def _assert_review_claim(case: Dict[str, Any], reviewer_id: str) -> None:
    claimed_by = case.get("review_claimed_by")
    if claimed_by and claimed_by != reviewer_id:
        raise ValueError("review_case_claimed_by_other")


def _count_by(conn: Any, store: LodiaStore, table_name: str, column_name: str) -> Dict[str, int]:
    table_name = _safe_metric_identifier(table_name)
    column_name = _safe_metric_identifier(column_name)
    rows = store._execute(conn, f"SELECT {column_name} AS key, COUNT(*) AS value FROM {table_name} GROUP BY {column_name}")
    return {row["key"]: row["value"] for row in rows}


def _owner_count(conn: Any, store: LodiaStore, table_name: str, owner_id: str) -> int:
    table_name = _safe_metric_identifier(table_name)
    row = store._get_one(conn, f"SELECT COUNT(*) AS value FROM {table_name} WHERE owner_id = ?", (owner_id,))
    return int(row["value"]) if row else 0


def _owner_count_grouped(conn: Any, store: LodiaStore, table_name: str, column_name: str, owner_id: str) -> Dict[str, int]:
    table_name = _safe_metric_identifier(table_name)
    column_name = _safe_metric_identifier(column_name)
    rows = store._execute(
        conn,
        f"""
        SELECT {column_name} AS key, COUNT(*) AS value
        FROM {table_name}
        WHERE owner_id = ?
        GROUP BY {column_name}
        """,
        (owner_id,),
    )
    return {str(row["key"] or "unknown"): int(row["value"]) for row in rows}


def _payout_totals_by_status(conn: Any, store: LodiaStore, contributor_id: str) -> Dict[str, Any]:
    rows = store._execute(
        conn,
        """
        SELECT status, COUNT(*) AS count, COALESCE(SUM(amount_cents), 0) AS amount
        FROM payout_events
        WHERE contributor_id = ?
        GROUP BY status
        """,
        (contributor_id,),
    )
    amounts: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    for row in rows:
        status = str(row["status"] or "unknown")
        amounts[status] = int(row["amount"] or 0)
        counts[status] = int(row["count"] or 0)
    return {
        "amounts": amounts,
        "counts": counts,
        "total_count": sum(counts.values()),
    }


def _dataset_artifact_descriptor(artifact: str) -> tuple[str, str, str]:
    try:
        return DATASET_ARTIFACTS[artifact]
    except KeyError as exc:
        raise ValueError("unsupported_dataset_artifact") from exc


def _simhash_from_dedup_json(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        simhash = loads(value).get("simhash")
        return int(simhash) if simhash is not None else None
    except (TypeError, ValueError):
        return None


def _email_domain(email: str) -> str:
    if "@" not in email:
        return "unknown"
    return email.rsplit("@", 1)[1][:120] or "unknown"


def _valid_contact_email(email: str) -> bool:
    if not email or email.count("@") != 1 or any(char.isspace() for char in email):
        return False
    local, domain = email.rsplit("@", 1)
    return bool(local and domain and "." in domain and not domain.startswith(".") and not domain.endswith("."))


def _clean_payout_status(value: str, allowed: set[str], error_code: str) -> str:
    clean = (value or "").strip().lower()
    if clean not in allowed:
        raise ValueError(error_code)
    return clean


def _clean_dispute_entity_type(value: str) -> str:
    clean = (value or "").strip().lower()
    allowed = {"enterprise_order", "dataset", "usage_event", "payout_event", "case", "delivery_grant"}
    if clean not in allowed:
        raise ValueError("invalid_dispute_entity_type")
    return clean


def _payout_profile_status(kyc_status: str, tax_status: str, risk_status: str) -> str:
    if _payout_profile_ready(
        {
            "kyc_status": kyc_status,
            "tax_status": tax_status,
            "risk_status": risk_status,
            "status": "active",
        }
    ):
        return "active"
    if kyc_status == "rejected" or tax_status == "rejected" or risk_status in {"blocked", "rejected"}:
        return "blocked"
    return "pending_review"


def _payout_profile_ready(profile: Dict[str, Any]) -> bool:
    return (
        profile.get("status") == "active"
        and profile.get("kyc_status") == "verified"
        and profile.get("tax_status") in {"verified", "not_required"}
        and profile.get("risk_status") == "clear"
    )


def _single_count(conn: Any, store: LodiaStore, table_name: str) -> int:
    table_name = _safe_metric_identifier(table_name)
    row = store._get_one(conn, f"SELECT COUNT(*) AS value FROM {table_name}")
    return int(row["value"]) if row else 0


def _sum_where(conn: Any, store: LodiaStore, table_name: str, amount_column: str, where_column: str, where_value: str) -> int:
    table_name = _safe_metric_identifier(table_name)
    amount_column = _safe_metric_identifier(amount_column)
    where_column = _safe_metric_identifier(where_column)
    row = store._get_one(
        conn,
        f"SELECT COALESCE(SUM({amount_column}), 0) AS value FROM {table_name} WHERE {where_column} = ?",
        (where_value,),
    )
    return int(row["value"]) if row else 0


def _count_grouped(conn: Any, store: LodiaStore, table_name: str, columns: List[str]) -> Dict[str, int]:
    safe_table = _safe_metric_identifier(table_name)
    safe_columns = [_safe_metric_identifier(column) for column in columns]
    select_columns = ", ".join(safe_columns)
    rows = store._execute(
        conn,
        f"SELECT {select_columns}, COUNT(*) AS count FROM {safe_table} GROUP BY {select_columns}",
    )
    result: Dict[str, int] = {}
    for row in rows:
        key = ":".join(str(row[column]) for column in safe_columns)
        result[key] = int(row["count"])
    return result


def _safe_metric_identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError("invalid_identifier")
    return value


def _label_value(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "_")


def _split_metric_key(value: str) -> tuple[str, str]:
    parts = value.split(":", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _vendor_data_category(task_type: str) -> str:
    if "extraction" in task_type:
        return "raw_asset_content"
    if "annotation" in task_type:
        return "redacted_case_content"
    return "operational_metadata"


def _vendor_processing_purpose(task_type: str) -> str:
    if "extraction" in task_type:
        return "multimodal_evidence_extraction"
    if "annotation" in task_type:
        return "structured_case_annotation"
    return "platform_processing"


def _page_bounds(limit: int, offset: int, max_limit: int) -> tuple[int, int]:
    return _bounded_limit(limit, max_limit), max(0, offset)


def _bounded_limit(limit: int, max_limit: int) -> int:
    return max(1, min(limit, max_limit))


def _bounded_score(score: float) -> float:
    return max(0.0, min(float(score), 1.0))


def _clean_roles(roles: List[str]) -> List[str]:
    allowed = {"admin", "reviewer", "contributor"}
    clean = sorted({role for role in roles if role in allowed})
    if not clean:
        raise ValueError("roles_required")
    return clean


def _clean_tenant_id(tenant_id: str) -> str:
    clean = (tenant_id or "default").strip().lower()
    clean = clean.replace("-", "_")
    if not clean.replace("_", "").isalnum() or len(clean) > 80:
        raise ValueError("invalid_tenant_id")
    return clean


def _clean_allowed_uses(allowed_uses: List[str]) -> List[str]:
    allowed = {
        "private_library",
        "candidate_pool",
        "commercial_dataset",
        "training",
        "gold_eval",
    }
    clean = sorted({use for use in allowed_uses if use in allowed})
    if not clean:
        raise ValueError("allowed_uses_required")
    return clean


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_start() -> str:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()


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


def _future_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))).isoformat()
