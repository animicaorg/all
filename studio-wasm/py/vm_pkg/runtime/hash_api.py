from __future__ import annotations

"""
hash_api — sha3/keccak wrappers (Pyodide-safe)

Goals
-----
- Provide deterministic, dependency-light hashing helpers usable in browsers via Pyodide.
- Prefer stdlib `hashlib` for SHA3 (available in CPython 3.8+ and Pyodide).
- Optionally use `pysha3` (module name: `sha3`) for Keccak-256. If unavailable, fall back to SHA3-256
  with a clear compatibility flag so callers can decide whether that's acceptable for their use-case.

API
---
- sha3_256(data: bytes) -> bytes
- sha3_512(data: bytes) -> bytes
- keccak256(data: bytes) -> bytes
- KECCAK_FALLBACK_TO_SHA3: bool  # True if keccak256 is aliased to sha3_256

All functions validate byte inputs and return raw bytes (no hex strings).
"""

import hashlib
from typing import Optional

# ---------------- Validation ----------------


def _ensure_bytes(name: str, v: bytes) -> bytes:
    if not isinstance(v, (bytes, bytearray)):
        raise TypeError(f"{name} must be bytes, got {type(v)}")
    return bytes(v)


# ---------------- SHA3 (stdlib) ----------------


def sha3_256(data: bytes) -> bytes:
    """One-shot SHA3-256; returns raw digest bytes."""
    d = _ensure_bytes("data", data)
    return hashlib.sha3_256(d).digest()


def sha3_512(data: bytes) -> bytes:
    """One-shot SHA3-512; returns raw digest bytes."""
    d = _ensure_bytes("data", data)
    return hashlib.sha3_512(d).digest()


# ---------------- Keccak-256 (pysha3 if present; otherwise SHA3-256 fallback) ----------------

_KECCAK_FN: Optional[object] = None
KECCAK_FALLBACK_TO_SHA3: bool = False

try:
    # The third-party `pysha3` package exposes Keccak as `sha3.keccak_256`
    # Pyodide typically doesn't bundle it; we treat absence as non-fatal.
    import sha3 as _pysha3  # type: ignore

    if hasattr(_pysha3, "keccak_256"):
        _KECCAK_FN = _pysha3.keccak_256  # type: ignore[attr-defined]
except Exception:
    _KECCAK_FN = None  # keep fallback

if _KECCAK_FN is None:
    KECCAK_FALLBACK_TO_SHA3 = True


def keccak256(data: bytes) -> bytes:
    """
    One-shot Keccak-256. If `pysha3` is not available, this falls back to SHA3-256
    and sets KECCAK_FALLBACK_TO_SHA3 = True. The fallback is deterministic, but
    cryptographically distinct from Keccak-256; use accordingly in simulations.
    """
    d = _ensure_bytes("data", data)
    if _KECCAK_FN is not None:
        h = _KECCAK_FN()  # type: ignore[operator]
        h.update(d)
        return h.digest()
    # Fallback: deterministic alias to SHA3-256
    return hashlib.sha3_256(d).digest()


# ---------------- Incremental helpers (optional) ----------------


class Sha3_256:
    """Incremental SHA3-256 helper (deterministic, Pyodide-safe)."""

    __slots__ = ("_h",)

    def __init__(self) -> None:
        self._h = hashlib.sha3_256()

    def update(self, data: bytes) -> None:
        self._h.update(_ensure_bytes("data", data))

    def digest(self) -> bytes:
        return self._h.digest()

    def hexdigest(self) -> str:
        return self._h.hexdigest()


class Sha3_512:
    """Incremental SHA3-512 helper (deterministic, Pyodide-safe)."""

    __slots__ = ("_h",)

    def __init__(self) -> None:
        self._h = hashlib.sha3_512()

    def update(self, data: bytes) -> None:
        self._h.update(_ensure_bytes("data", data))

    def digest(self) -> bytes:
        return self._h.digest()

    def hexdigest(self) -> str:
        return self._h.hexdigest()


class Keccak256:
    """
    Incremental Keccak-256. Falls back to SHA3-256 if `pysha3` is not present.
    Check `KECCAK_FALLBACK_TO_SHA3` if you need to assert true Keccak usage.
    """

    __slots__ = ("_impl",)

    def __init__(self) -> None:
        if _KECCAK_FN is not None:
            self._impl = _KECCAK_FN()  # type: ignore[operator]
        else:
            self._impl = hashlib.sha3_256()

    def update(self, data: bytes) -> None:
        self._impl.update(_ensure_bytes("data", data))

    def digest(self) -> bytes:
        return self._impl.digest()

    def hexdigest(self) -> str:
        return self._impl.hexdigest()


__all__ = [
    "sha3_256",
    "sha3_512",
    "keccak256",
    "Sha3_256",
    "Sha3_512",
    "Keccak256",
    "KECCAK_FALLBACK_TO_SHA3",
]
