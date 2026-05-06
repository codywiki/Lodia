from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


def _csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _int(value: Optional[str], default: int, minimum: int = 1) -> int:
    if not value:
        return default
    try:
        return max(minimum, int(value))
    except ValueError:
        return default


@dataclass(frozen=True)
class LodiaSettings:
    env: str
    region: str
    data_dir: Path
    database_url: Optional[str]
    db_pool_min_size: int
    db_pool_max_size: int
    redis_url: Optional[str]
    queue_backend: str
    async_processing: bool
    object_storage_backend: str
    object_storage_dir: Path
    s3_bucket: Optional[str]
    s3_endpoint_url: Optional[str]
    s3_region: Optional[str]
    s3_prefix: str
    s3_sse_algorithm: str
    s3_kms_key_id: Optional[str]
    cors_origins: List[str]
    auth_token_specs: List[str]
    password_pepper: str
    raw_object_ttl_hours: int
    max_asset_bytes: int
    max_request_body_bytes: int
    rate_limit_enabled: bool
    rate_limit_requests: int
    rate_limit_window_seconds: int
    trust_proxy_headers: bool
    require_request_signature: bool
    request_signature_secret: Optional[str]
    max_page_limit: int
    dataset_max_cases: int

    @classmethod
    def from_env(cls, data_dir: Optional[str] = None) -> "LodiaSettings":
        root = Path(data_dir or os.environ.get("LODIA_DATA_DIR", "storage/dev"))
        env = os.environ.get("LODIA_ENV", "development")
        return cls(
            env=env,
            region=os.environ.get("LODIA_REGION", "CN"),
            data_dir=root,
            database_url=os.environ.get("POSTGRES_DSN") or os.environ.get("DATABASE_URL"),
            db_pool_min_size=_int(os.environ.get("LODIA_DB_POOL_MIN_SIZE"), 1),
            db_pool_max_size=_int(os.environ.get("LODIA_DB_POOL_MAX_SIZE"), 10),
            redis_url=os.environ.get("REDIS_URL"),
            queue_backend=os.environ.get("LODIA_QUEUE_BACKEND", "database").lower(),
            async_processing=_bool(os.environ.get("LODIA_ASYNC_PROCESSING")),
            object_storage_backend=os.environ.get("LODIA_OBJECT_STORAGE_BACKEND", "local").lower(),
            object_storage_dir=Path(os.environ.get("LODIA_OBJECT_STORAGE_DIR", str(root))),
            s3_bucket=os.environ.get("LODIA_S3_BUCKET"),
            s3_endpoint_url=os.environ.get("LODIA_S3_ENDPOINT_URL"),
            s3_region=os.environ.get("LODIA_S3_REGION"),
            s3_prefix=os.environ.get("LODIA_S3_PREFIX", "lodia"),
            s3_sse_algorithm=os.environ.get("LODIA_S3_SSE_ALGORITHM") or "AES256",
            s3_kms_key_id=os.environ.get("LODIA_S3_KMS_KEY_ID"),
            cors_origins=_csv(
                os.environ.get(
                    "LODIA_ALLOWED_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173",
                )
            ),
            auth_token_specs=_auth_token_specs(),
            password_pepper=os.environ.get("LODIA_PASSWORD_PEPPER", ""),
            raw_object_ttl_hours=_int(os.environ.get("LODIA_RAW_OBJECT_TTL_HOURS"), 24),
            max_asset_bytes=_int(os.environ.get("LODIA_MAX_ASSET_BYTES"), 1_048_576),
            max_request_body_bytes=_int(os.environ.get("LODIA_MAX_REQUEST_BODY_BYTES"), 1_048_576),
            rate_limit_enabled=_bool(
                os.environ.get("LODIA_RATE_LIMIT_ENABLED"),
                default=env.lower() == "production",
            ),
            rate_limit_requests=_int(os.environ.get("LODIA_RATE_LIMIT_REQUESTS"), 120),
            rate_limit_window_seconds=_int(os.environ.get("LODIA_RATE_LIMIT_WINDOW_SECONDS"), 60),
            trust_proxy_headers=_bool(os.environ.get("LODIA_TRUST_PROXY_HEADERS")),
            require_request_signature=_bool(os.environ.get("LODIA_REQUIRE_REQUEST_SIGNATURE")),
            request_signature_secret=os.environ.get("LODIA_REQUEST_SIGNATURE_SECRET"),
            max_page_limit=_int(os.environ.get("LODIA_MAX_PAGE_LIMIT"), 500),
            dataset_max_cases=_int(os.environ.get("LODIA_DATASET_MAX_CASES"), 5_000),
        )

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def use_postgres(self) -> bool:
        return bool(self.database_url and self.database_url.startswith(("postgres://", "postgresql://")))


def _auth_token_specs() -> List[str]:
    specs: List[str] = []
    admin = os.environ.get("LODIA_ADMIN_TOKEN")
    reviewer = os.environ.get("LODIA_REVIEWER_TOKEN")
    contributor = os.environ.get("LODIA_CONTRIBUTOR_TOKEN")
    if admin:
        specs.append(f"{admin}:admin,reviewer,contributor:admin")
    if reviewer:
        specs.append(f"{reviewer}:reviewer:reviewer")
    if contributor:
        specs.append(f"{contributor}:contributor:contributor")
    specs.extend(_csv(os.environ.get("LODIA_AUTH_TOKENS", "")))
    return specs
