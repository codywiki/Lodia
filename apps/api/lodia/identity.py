from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 310_000


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str, pepper: str = "") -> str:
    if len(password) < 10:
        raise ValueError("password_too_short")
    salt = secrets.token_bytes(16)
    digest = _derive_password(password, salt, pepper, PASSWORD_ITERATIONS)
    return "$".join(
        [
            PASSWORD_ALGORITHM,
            str(PASSWORD_ITERATIONS),
            _b64(salt),
            _b64(digest),
        ]
    )


def verify_password(password: str, password_hash: str, pepper: str = "") -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = _unb64(salt_text)
        expected = _unb64(digest_text)
    except (ValueError, TypeError):
        return False

    actual = _derive_password(password, salt, pepper, iterations)
    return hmac.compare_digest(actual, expected)


def new_api_token() -> str:
    return f"lod_{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_suffix(token: str) -> str:
    return token[-8:] if len(token) >= 8 else token


def _derive_password(password: str, salt: bytes, pepper: str, iterations: int) -> bytes:
    value = f"{password}{pepper}".encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", value, salt, iterations)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
