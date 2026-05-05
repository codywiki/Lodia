from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from .config import LodiaSettings


class JobQueue(Protocol):
    def publish(self, queue_name: str, job_id: str) -> None:
        ...

    def pop(self, queue_name: str, timeout_seconds: int = 0) -> Optional[str]:
        ...

    def health_check(self) -> Dict[str, Any]:
        ...


@dataclass
class DatabaseJobQueue:
    def publish(self, queue_name: str, job_id: str) -> None:
        return None

    def pop(self, queue_name: str, timeout_seconds: int = 0) -> Optional[str]:
        return None

    def health_check(self) -> Dict[str, Any]:
        return {"ok": True, "backend": "database"}


class RedisJobQueue:
    def __init__(self, redis_url: str):
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("redis is required when LODIA_QUEUE_BACKEND=redis") from exc
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def publish(self, queue_name: str, job_id: str) -> None:
        self.client.lpush(_queue_key(queue_name), job_id)

    def pop(self, queue_name: str, timeout_seconds: int = 0) -> Optional[str]:
        key = _queue_key(queue_name)
        if timeout_seconds > 0:
            result = self.client.brpop(key, timeout=timeout_seconds)
            return result[1] if result else None
        return self.client.rpop(key)

    def health_check(self) -> Dict[str, Any]:
        self.client.ping()
        return {"ok": True, "backend": "redis"}


def create_job_queue(settings: LodiaSettings) -> JobQueue:
    if settings.queue_backend == "redis":
        if not settings.redis_url:
            raise ValueError("REDIS_URL is required when LODIA_QUEUE_BACKEND=redis")
        return RedisJobQueue(settings.redis_url)
    return DatabaseJobQueue()


def _queue_key(queue_name: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in queue_name)
    return f"lodia:jobs:{safe}"
