from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from starlette.requests import Request


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    retry_after: int


class FixedWindowRateLimiter:
    """Small app-level limiter for single-node safety and tests.

    Multi-node production should still enforce limits at the gateway or Redis
    layer. This limiter provides a defensive local backstop for every API node.
    """

    def __init__(self, requests: int, window_seconds: int, enabled: bool = True, max_buckets: int = 100_000):
        self.requests = max(1, requests)
        self.window_seconds = max(1, window_seconds)
        self.enabled = enabled
        self.max_buckets = max(1_000, max_buckets)
        self._lock = threading.Lock()
        self._buckets: Dict[str, Tuple[int, int]] = {}

    def check(self, key: str, now: Optional[float] = None) -> RateLimitResult:
        current = int(now if now is not None else time.time())
        window_start = current - (current % self.window_seconds)
        reset_at = window_start + self.window_seconds

        if not self.enabled:
            return RateLimitResult(True, self.requests, self.requests, reset_at, 0)

        with self._lock:
            if len(self._buckets) > self.max_buckets:
                self._buckets = {
                    bucket_key: bucket_value
                    for bucket_key, bucket_value in self._buckets.items()
                    if bucket_value[1] == window_start
                }

            count, existing_start = self._buckets.get(key, (0, window_start))
            if existing_start != window_start:
                count = 0
                existing_start = window_start

            allowed = count < self.requests
            if allowed:
                count += 1
                self._buckets[key] = (count, existing_start)

            retry_after = max(1, reset_at - current) if not allowed else 0
            remaining = max(0, self.requests - count)
            return RateLimitResult(allowed, self.requests, remaining, reset_at, retry_after)


def rate_limit_key(request: Request, trust_proxy_headers: bool = False) -> str:
    authorization = request.headers.get("authorization", "")
    token = _bearer_token(authorization)
    if token:
        return f"token:{hashlib.sha256(token.encode('utf-8')).hexdigest()[:24]}"

    forwarded_for = request.headers.get("x-forwarded-for", "") if trust_proxy_headers else ""
    if forwarded_for:
        client = forwarded_for.split(",", 1)[0].strip()
    elif request.client:
        client = request.client.host
    else:
        client = "unknown"
    return f"ip:{hashlib.sha256(client.encode('utf-8')).hexdigest()[:24]}"


def _bearer_token(authorization: str) -> Optional[str]:
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    token = authorization[len(prefix) :].strip()
    return token or None
