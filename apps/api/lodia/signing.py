from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Optional


SIGNATURE_TOLERANCE_SECONDS = 300


@dataclass(frozen=True)
class SignatureResult:
    ok: bool
    reason: str = ""


def verify_request_signature(
    *,
    secret: Optional[str],
    method: str,
    path: str,
    body: bytes,
    timestamp: Optional[str],
    signature: Optional[str],
    now: Optional[int] = None,
) -> SignatureResult:
    if not secret:
        return SignatureResult(False, "signature_secret_not_configured")
    if not timestamp or not signature:
        return SignatureResult(False, "missing_signature")
    try:
        issued_at = int(timestamp)
    except ValueError:
        return SignatureResult(False, "invalid_signature_timestamp")

    current = int(now if now is not None else time.time())
    if abs(current - issued_at) > SIGNATURE_TOLERANCE_SECONDS:
        return SignatureResult(False, "signature_timestamp_out_of_range")

    body_hash = hashlib.sha256(body).hexdigest()
    signed = f"{timestamp}.{method.upper()}.{path}.{body_hash}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return SignatureResult(False, "invalid_signature")
    return SignatureResult(True)
