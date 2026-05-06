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

    def put_bytes(self, key: str, value: bytes, content_type: str = "application/octet-stream") -> ObjectRef:
        ...

    def read_text(self, uri: str) -> str:
        ...

    def read_bytes(self, uri: str) -> bytes:
        ...

    def delete(self, uri: str) -> None:
        ...

    def object_uri(self, key: str) -> str:
        ...

    def presign_put(self, key: str, content_type: str, expires_in_seconds: int) -> Dict[str, Any]:
        ...

    def health_check(self) -> Dict[str, Any]:
        ...


class LocalObjectStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_text(self, key: str, value: str) -> ObjectRef:
        return self.put_bytes(key, value.encode("utf-8"), "text/plain; charset=utf-8")

    def put_bytes(self, key: str, value: bytes, content_type: str = "application/octet-stream") -> ObjectRef:
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        path.chmod(0o600)
        return ObjectRef(uri=str(path), key=key)

    def read_text(self, uri: str) -> str:
        return self.read_bytes(uri).decode("utf-8")

    def read_bytes(self, uri: str) -> bytes:
        return self._path_for_uri(uri).read_bytes()

    def delete(self, uri: str) -> None:
        path = self._path_for_uri(uri)
        if path.exists():
            path.unlink()

    def object_uri(self, key: str) -> str:
        return str(self._path_for_key(key))

    def presign_put(self, key: str, content_type: str, expires_in_seconds: int) -> Dict[str, Any]:
        return {
            "method": "PUT",
            "url": "",
            "headers": {"Content-Type": content_type},
            "expires_in_seconds": expires_in_seconds,
            "direct_upload_supported": False,
            "object_uri": self.object_uri(key),
            "object_key": _clean_key(key),
        }

    def health_check(self) -> Dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        return {
            "ok": self.root.exists() and os.access(self.root, os.W_OK),
            "backend": "local",
            "root": str(self.root),
        }

    def _path_for_key(self, key: str) -> Path:
        path = (self.root / _clean_key(key)).resolve()
        return _ensure_under_root(self.root, path)

    def _path_for_uri(self, uri: str) -> Path:
        path = Path(uri).resolve()
        return _ensure_under_root(self.root, path)


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
        return self.put_bytes(key, value.encode("utf-8"), "text/plain; charset=utf-8")

    def put_bytes(self, key: str, value: bytes, content_type: str = "application/octet-stream") -> ObjectRef:
        clean = _clean_key(key)
        object_key = f"{self.prefix}/{clean}" if self.prefix else clean
        put_kwargs = {
            "Bucket": self.bucket,
            "Key": object_key,
            "Body": value,
            "ContentType": content_type,
        }
        if self.sse_algorithm:
            put_kwargs["ServerSideEncryption"] = self.sse_algorithm
            if self.sse_algorithm == "aws:kms" and self.kms_key_id:
                put_kwargs["SSEKMSKeyId"] = self.kms_key_id
        self.client.put_object(**put_kwargs)
        return ObjectRef(uri=f"s3://{self.bucket}/{object_key}", key=object_key)

    def read_text(self, uri: str) -> str:
        return self.read_bytes(uri).decode("utf-8")

    def read_bytes(self, uri: str) -> bytes:
        key = self._key_from_uri(uri)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def delete(self, uri: str) -> None:
        key = self._key_from_uri(uri)
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def object_uri(self, key: str) -> str:
        clean = _clean_key(key)
        object_key = f"{self.prefix}/{clean}" if self.prefix else clean
        return f"s3://{self.bucket}/{object_key}"

    def presign_put(self, key: str, content_type: str, expires_in_seconds: int) -> Dict[str, Any]:
        clean = _clean_key(key)
        object_key = f"{self.prefix}/{clean}" if self.prefix else clean
        params = {"Bucket": self.bucket, "Key": object_key, "ContentType": content_type}
        headers = {"Content-Type": content_type}
        if self.sse_algorithm:
            params["ServerSideEncryption"] = self.sse_algorithm
            headers["x-amz-server-side-encryption"] = self.sse_algorithm
            if self.sse_algorithm == "aws:kms" and self.kms_key_id:
                params["SSEKMSKeyId"] = self.kms_key_id
                headers["x-amz-server-side-encryption-aws-kms-key-id"] = self.kms_key_id
        return {
            "method": "PUT",
            "url": self.client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=expires_in_seconds,
            ),
            "headers": headers,
            "expires_in_seconds": expires_in_seconds,
            "direct_upload_supported": True,
            "object_uri": f"s3://{self.bucket}/{object_key}",
            "object_key": object_key,
        }

    def health_check(self) -> Dict[str, Any]:
        self.client.head_bucket(Bucket=self.bucket)
        return {
            "ok": True,
            "backend": "s3",
            "bucket": self.bucket,
            "prefix": self.prefix,
            "sse": self.sse_algorithm,
        }

    def _key_from_uri(self, uri: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("unsupported_s3_uri")
        key = uri[len(prefix) :]
        if self.prefix and not (key == self.prefix or key.startswith(f"{self.prefix}/")):
            raise ValueError("s3_key_outside_prefix")
        return key


def create_object_storage(settings: LodiaSettings) -> ObjectStorage:
    if settings.object_storage_backend == "s3":
        return S3ObjectStorage(settings)
    return LocalObjectStorage(settings.object_storage_dir)


def _clean_key(key: str) -> str:
    clean = "/".join(part for part in key.split("/") if part not in {"", ".", ".."})
    return clean or "object.bin"


def _ensure_under_root(root: Path, path: Path) -> Path:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("local_object_outside_root") from exc
    return path
