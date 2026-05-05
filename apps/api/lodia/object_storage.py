from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Protocol

from .config import LodiaSettings


@dataclass(frozen=True)
class ObjectRef:
    uri: str
    key: str


class ObjectStorage(Protocol):
    def put_text(self, key: str, value: str) -> ObjectRef:
        ...

    def read_text(self, uri: str) -> str:
        ...

    def delete(self, uri: str) -> None:
        ...

    def health_check(self) -> Dict[str, Any]:
        ...


class LocalObjectStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_text(self, key: str, value: str) -> ObjectRef:
        path = self.root / _clean_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        return ObjectRef(uri=str(path), key=key)

    def read_text(self, uri: str) -> str:
        return Path(uri).read_text(encoding="utf-8")

    def delete(self, uri: str) -> None:
        path = Path(uri)
        if path.exists():
            path.unlink()

    def health_check(self) -> Dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        return {
            "ok": self.root.exists() and os.access(self.root, os.W_OK),
            "backend": "local",
            "root": str(self.root),
        }


class S3ObjectStorage:
    def __init__(self, settings: LodiaSettings):
        if not settings.s3_bucket:
            raise ValueError("LODIA_S3_BUCKET is required for s3 object storage")
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for s3 object storage") from exc
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix.strip("/")
        self.sse_algorithm = settings.s3_sse_algorithm
        self.kms_key_id = settings.s3_kms_key_id
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
        )

    def put_text(self, key: str, value: str) -> ObjectRef:
        clean = _clean_key(key)
        object_key = f"{self.prefix}/{clean}" if self.prefix else clean
        put_kwargs = {
            "Bucket": self.bucket,
            "Key": object_key,
            "Body": value.encode("utf-8"),
            "ContentType": "text/plain; charset=utf-8",
        }
        if self.sse_algorithm:
            put_kwargs["ServerSideEncryption"] = self.sse_algorithm
            if self.sse_algorithm == "aws:kms" and self.kms_key_id:
                put_kwargs["SSEKMSKeyId"] = self.kms_key_id
        self.client.put_object(**put_kwargs)
        return ObjectRef(uri=f"s3://{self.bucket}/{object_key}", key=object_key)

    def read_text(self, uri: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("unsupported_s3_uri")
        key = uri[len(prefix) :]
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read().decode("utf-8")

    def delete(self, uri: str) -> None:
        prefix = f"s3://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("unsupported_s3_uri")
        key = uri[len(prefix) :]
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def health_check(self) -> Dict[str, Any]:
        self.client.head_bucket(Bucket=self.bucket)
        return {
            "ok": True,
            "backend": "s3",
            "bucket": self.bucket,
            "prefix": self.prefix,
            "sse": self.sse_algorithm,
        }


def create_object_storage(settings: LodiaSettings) -> ObjectStorage:
    if settings.object_storage_backend == "s3":
        return S3ObjectStorage(settings)
    return LocalObjectStorage(settings.object_storage_dir)


def _clean_key(key: str) -> str:
    return "/".join(part for part in key.split("/") if part not in {"", ".", ".."})
