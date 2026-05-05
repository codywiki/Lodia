from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


def _csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class LodiaSettings:
    env: str
    region: str
    data_dir: Path
    database_url: Optional[str]
    async_processing: bool
    object_storage_backend: str
    object_storage_dir: Path
    s3_bucket: Optional[str]
    s3_endpoint_url: Optional[str]
    s3_region: Optional[str]
    s3_prefix: str
    cors_origins: List[str]
    auth_token_specs: List[str]

    @classmethod
    def from_env(cls, data_dir: Optional[str] = None) -> "LodiaSettings":
        root = Path(data_dir or os.environ.get("LODIA_DATA_DIR", "storage/dev"))
        return cls(
            env=os.environ.get("LODIA_ENV", "development"),
            region=os.environ.get("LODIA_REGION", "CN"),
            data_dir=root,
            database_url=os.environ.get("POSTGRES_DSN") or os.environ.get("DATABASE_URL"),
            async_processing=os.environ.get("LODIA_ASYNC_PROCESSING", "false").lower() == "true",
            object_storage_backend=os.environ.get("LODIA_OBJECT_STORAGE_BACKEND", "local").lower(),
            object_storage_dir=Path(os.environ.get("LODIA_OBJECT_STORAGE_DIR", str(root))),
            s3_bucket=os.environ.get("LODIA_S3_BUCKET"),
            s3_endpoint_url=os.environ.get("LODIA_S3_ENDPOINT_URL"),
            s3_region=os.environ.get("LODIA_S3_REGION"),
            s3_prefix=os.environ.get("LODIA_S3_PREFIX", "lodia"),
            cors_origins=_csv(
                os.environ.get(
                    "LODIA_ALLOWED_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173",
                )
            ),
            auth_token_specs=_auth_token_specs(),
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
