from __future__ import annotations

"""
stdlib.storage — simple key/value helpers for contracts in the browser simulator.

This module wraps the runtime storage API with a friendlier surface and a tiny,
explicit encoding for common scalar types. All functions are deterministic and
perform basic validation.

Storage model (simulator)
-------------------------
- Keys and values are opaque bytes.
- Reads of missing keys return `None` at the raw layer; helpers may accept a
  default and return that instead.
- Integers are encoded as fixed 32-byte big-endian unsigned values (u256).

API
---
Raw:
    get_raw(key: bytes) -> bytes | None
    set_raw(key: bytes, val: bytes) -> None
    delete(key: bytes) -> None
    exists(key: bytes) -> bool

Bytes helpers:
    get_bytes(key: bytes, default: bytes = b"") -> bytes
    set_bytes(key: bytes, val: bytes) -> None

U256 helpers:
    get_u256(key: bytes, default: int = 0) -> int
    set_u256(key: bytes, n: int) -> None
"""

from typing import Optional

from ..errors import ValidationError
from ..runtime import storage_api


# ---------------- Validation ----------------

_MAX_KEY_LEN = 128
_MAX_VAL_LEN = 256 * 1024  # 256 KiB for simulator


def _ensure_bytes(name: str, v) -> bytes:
    if not isinstance(v, (bytes, bytearray)):
        raise ValidationError(f"{name} must be bytes")
    return bytes(v)


def _check_key(key: bytes) -> bytes:
    key = _ensure_bytes("key", key)
    if len(key) == 0:
        raise ValidationError("key must be non-empty")
    if len(key) > _MAX_KEY_LEN:
        raise ValidationError("key too long")
    return key


def _check_val(val: bytes) -> bytes:
    val = _ensure_bytes("value", val)
    if len(val) > _MAX_VAL_LEN:
        raise ValidationError("value too large for simulator")
    return val


# ---------------- Raw ops ----------------

def get_raw(key: bytes) -> Optional[bytes]:
    """Return raw value for key, or None if missing."""
    key = _check_key(key)
    return storage_api.get(key)


def set_raw(key: bytes, val: bytes) -> None:
    """Set raw value for key (overwrites any existing value)."""
    key = _check_key(key)
    val = _check_val(val)
    storage_api.set(key, val)


def delete(key: bytes) -> None:
    """Delete key if present."""
    key = _check_key(key)
    storage_api.delete(key)


def exists(key: bytes) -> bool:
    """True if key is present."""
    key = _check_key(key)
    return storage_api.get(key) is not None


# ---------------- Bytes helpers ----------------

def get_bytes(key: bytes, default: bytes = b"") -> bytes:
    """
    Get bytes value; returns `default` if the key is missing.
    """
    key = _check_key(key)
    default = _ensure_bytes("default", default)
    val = storage_api.get(key)
    return val if val is not None else default


def set_bytes(key: bytes, val: bytes) -> None:
    """
    Set bytes value.
    """
    set_raw(key, val)


# ---------------- U256 helpers ----------------

_U256_MAX = (1 << 256) - 1


def _u256_to_bytes32(n: int) -> bytes:
    if not isinstance(n, int) or n < 0:
        raise ValidationError("u256 must be a non-negative int")
    if n > _U256_MAX:
        raise ValidationError("u256 overflow")
    return n.to_bytes(32, "big")


def _bytes_to_u256(b: bytes) -> int:
    b = _ensure_bytes("stored u256", b)
    if len(b) != 32:
        raise ValidationError("stored u256 must be exactly 32 bytes")
    out = 0
    for byte in b:
        out = (out << 8) | byte
    return out


def get_u256(key: bytes, default: int = 0) -> int:
    """
    Read a u256 value encoded as 32-byte big-endian.
    If the key is missing, returns `default` (validated to be u256).
    """
    key = _check_key(key)
    if not isinstance(default, int) or default < 0 or default > _U256_MAX:
        raise ValidationError("default must be a u256")
    val = storage_api.get(key)
    if val is None:
        return default
    return _bytes_to_u256(val)


def set_u256(key: bytes, n: int) -> None:
    """
    Write a u256 value encoded as 32-byte big-endian.
    """
    key = _check_key(key)
    storage_api.set(key, _u256_to_bytes32(n))


__all__ = [
    "get_raw",
    "set_raw",
    "delete",
    "exists",
    "get_bytes",
    "set_bytes",
    "get_u256",
    "set_u256",
]
