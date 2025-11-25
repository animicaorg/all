from __future__ import annotations

from typing import Dict, Optional

# Simple in-memory key/value store for example contracts and tests.
# Keys and values are always bytes.


_STORE: Dict[bytes, bytes] = {}


def _ensure_bytes(name: str, value: object) -> bytes:
    if not isinstance(value, (bytes, bytearray)):
        raise TypeError(f"{name} must be bytes, got {type(value).__name__}")
    return bytes(value)


def reset_backend() -> None:
    """
    Test-only helper: clear the in-memory store so each test starts
    from a clean slate.
    """
    _STORE.clear()


def get(key: bytes, default: Optional[bytes] = None) -> bytes:
    """
    Get the value stored at 'key'. If the key is missing:

      * if 'default' is provided, that default (as bytes) is returned
      * otherwise, an empty byte string is returned (b"")
    """
    bkey = _ensure_bytes("key", key)
    if bkey in _STORE:
        return _STORE[bkey]
    if default is not None:
        return _ensure_bytes("default", default)
    return b""


def set(key: bytes, value: bytes) -> None:
    """Store 'value' at 'key' deterministically."""
    bkey = _ensure_bytes("key", key)
    bval = _ensure_bytes("value", value)
    _STORE[bkey] = bval


def delete(key: bytes) -> None:
    """Delete 'key' if present (no-op if absent)."""
    bkey = _ensure_bytes("key", key)
    _STORE.pop(bkey, None)


__all__ = ["reset_backend", "get", "set", "delete"]
