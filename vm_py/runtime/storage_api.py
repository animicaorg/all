"""
vm_py.runtime.storage_api — host hooks for deterministic key/value storage.

This module provides the contract-facing storage primitives that the
synthetic `stdlib.storage` re-exports (via vm_py.runtime.sandbox).

Design goals
------------
- Deterministic: pure functions over (key, value) with no wall-clock or I/O.
- Simple default: in-process memory backend for local runs & tests.
- Pluggable: a tiny backend interface so the host can swap in a real state DB.
- Safe: strict byte-length caps; typed helpers for common int ↔ bytes use.

Public API (re-exported by stdlib.storage)
------------------------------------------
- get(key: bytes) -> Optional[bytes]
- set(key: bytes, value: bytes) -> None
- delete(key: bytes) -> None
- exists(key: bytes) -> bool
- get_int(key: bytes) -> Optional[int]           # big-endian, unsigned
- set_int(key: bytes, value: int) -> None        # big-endian, unsigned

Host API (optional)
-------------------
- set_backend(backend: StorageBackend) -> None
- reset_backend() -> None

Notes
-----
Gas charging (if any) is accounted for in the VM engine; these hooks are
intentionally thin. Length caps are read from vm_py.config if present.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional, Protocol, runtime_checkable

try:
    # Soft imports; provide sane defaults if not defined.
    from vm_py.errors import VmError
except Exception:  # pragma: no cover - during bootstrap

    class VmError(Exception):  # type: ignore
        pass


try:
    import vm_py.config as _cfg
except Exception:  # pragma: no cover

    class _cfg:  # type: ignore
        MAX_STORAGE_KEY_BYTES = 64
        MAX_STORAGE_VALUE_BYTES = 64 * 1024


# ---------------------------- Backend API ---------------------------- #


@runtime_checkable
class StorageBackend(Protocol):
    """Minimal backend interface for contract storage."""

    def get(self, key: bytes) -> Optional[bytes]: ...
    def set(self, key: bytes, value: bytes) -> None: ...
    def delete(self, key: bytes) -> None: ...
    def exists(self, key: bytes) -> bool: ...


class _MemoryBackend(StorageBackend):
    """Thread-safe in-memory backend for local runs and tests."""

    def __init__(self) -> None:
        self._store: Dict[bytes, bytes] = {}
        self._lock = threading.RLock()

    def get(self, key: bytes) -> Optional[bytes]:
        with self._lock:
            return self._store.get(key)

    def set(self, key: bytes, value: bytes) -> None:
        with self._lock:
            self._store[key] = value

    def delete(self, key: bytes) -> None:
        with self._lock:
            self._store.pop(key, None)

    def exists(self, key: bytes) -> bool:
        with self._lock:
            return key in self._store


_backend: StorageBackend = _MemoryBackend()


def set_backend(backend: StorageBackend) -> None:
    """Install a custom backend (host integration)."""
    global _backend
    if not isinstance(backend, StorageBackend.__constraints__ if hasattr(StorageBackend, "__constraints__") else StorageBackend):  # type: ignore[attr-defined]
        # A defensive check for Protocol conformance in runtime; mypy enforces at type-check time.
        # Fallback because `isinstance(obj, Protocol)` is not always supported.
        for attr in ("get", "set", "delete", "exists"):
            if not hasattr(backend, attr):
                raise VmError(f"backend missing method: {attr}")
    _backend = backend


def reset_backend() -> None:
    """Restore the default in-memory backend (useful for tests)."""
    set_backend(_MemoryBackend())


# --------------------------- Validation helpers --------------------------- #


def _check_key(key: bytes) -> None:
    if not isinstance(key, (bytes, bytearray)):
        raise VmError("storage key must be bytes")
    if len(key) == 0:
        raise VmError("storage key must be non-empty")
    if len(key) > getattr(_cfg, "MAX_STORAGE_KEY_BYTES", 64):
        raise VmError(
            f"storage key too long (>{getattr(_cfg, 'MAX_STORAGE_KEY_BYTES', 64)} bytes)"
        )


def _check_value(value: bytes) -> None:
    if not isinstance(value, (bytes, bytearray)):
        raise VmError("storage value must be bytes")
    max_len = getattr(_cfg, "MAX_STORAGE_VALUE_BYTES", 64 * 1024)
    if len(value) > max_len:
        raise VmError(f"storage value too large (>{max_len} bytes)")


# --------------------------- Contract-facing API --------------------------- #


def get(key: bytes) -> Optional[bytes]:
    """Return the value for `key`, or None if not set."""
    _check_key(key)
    return _backend.get(bytes(key))


def set(key: bytes, value: bytes) -> None:
    """Set `key` to `value` (overwrites existing)."""
    _check_key(key)
    _check_value(value)
    _backend.set(bytes(key), bytes(value))


def delete(key: bytes) -> None:
    """Delete `key` if present (no-op otherwise)."""
    _check_key(key)
    _backend.delete(bytes(key))


def exists(key: bytes) -> bool:
    """Return True if `key` is present."""
    _check_key(key)
    return _backend.exists(bytes(key))


# ------------------------------ Typed helpers ----------------------------- #

_U256_MAX = (1 << 256) - 1


def get_int(key: bytes) -> Optional[int]:
    """
    Read big-endian unsigned integer at `key`. Returns None if not set.
    Empty value is treated as 0 (but we never write empty for ints).
    """
    raw = get(key)
    if raw is None:
        return None
    if len(raw) == 0:
        return 0
    return int.from_bytes(raw, byteorder="big", signed=False)


def set_int(key: bytes, value: int) -> None:
    """
    Store `value` as big-endian unsigned integer. Enforces 0 <= value <= 2^256-1.
    """
    if not isinstance(value, int):
        raise VmError("set_int value must be int")
    if value < 0 or value > _U256_MAX:
        raise VmError("set_int out of range (must fit in 256 bits)")
    # Minimal bytes representation (zero -> b"\x00")
    if value == 0:
        encoded = b"\x00"
    else:
        width = (value.bit_length() + 7) // 8
        encoded = value.to_bytes(width, "big")
    set(key, encoded)


__all__ = [
    "StorageBackend",
    "set_backend",
    "reset_backend",
    "get",
    "set",
    "delete",
    "exists",
    "get_int",
    "set_int",
]
