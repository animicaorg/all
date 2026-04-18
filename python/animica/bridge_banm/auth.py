from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any


PBKDF2_ITERATIONS = 600_000


def hash_password(password: str, *, salt: str | None = None) -> tuple[str, str]:
    resolved_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        resolved_salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return resolved_salt, digest.hex()


def verify_password(password: str, *, salt: str, password_hash: str) -> bool:
    _, candidate = hash_password(password, salt=salt)
    return hmac.compare_digest(candidate, password_hash)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def sign_token(payload: dict[str, Any], *, secret: str, expires_in_seconds: int = 3600) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + int(expires_in_seconds)
    serialized = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64(serialized)
    signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64(signature)}"


def verify_token(token: str, *, secret: str) -> dict[str, Any]:
    try:
        payload_b64, signature_b64 = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid token format") from exc
    expected = hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    provided = _unb64(signature_b64)
    if not hmac.compare_digest(expected, provided):
        raise ValueError("invalid token signature")
    payload = json.loads(_unb64(payload_b64).decode("utf-8"))
    exp = int(payload.get("exp") or 0)
    if exp < int(time.time()):
        raise ValueError("token expired")
    return payload


@dataclass(frozen=True)
class AdminPrincipal:
    username: str
    role: str

