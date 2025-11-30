"""
vm_py.runtime.hash_api — deterministic hashing wrappers for the VM runtime.

Goals
-----
- Strictly bytes-in, bytes-out (no implicit text/encoding).
- Optional domain separation prefix for safer composition across subsystems.
- Minimal dependencies; Keccak-256 is optional with helpful error if missing.

Provided APIs
-------------
- sha3_256(data: bytes, *, domain: bytes = b"") -> bytes
- sha3_512(data: bytes, *, domain: bytes = b"") -> bytes
- keccak256(data: bytes, *, domain: bytes = b"") -> bytes           # requires pysha3 or pycryptodome
- sha3_256_hex(...), sha3_512_hex(...), keccak256_hex(...)
- hash_concat_{sha3_256,sha3_512,keccak256}(*chunks: bytes, domain=b"") -> bytes
- Streaming hashers: Sha3_256(), Sha3_512(), Keccak256()  (uniform interface)

Domain Separation
-----------------
If a non-empty `domain` is provided, the hash input becomes:

    b"\\x19animica:" || domain || b"\\x00" || data

This avoids ambiguous concatenations while keeping a compact, readable tag.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Protocol, runtime_checkable

# ------------------------------ Errors & Config ------------------------------ #

try:
    from vm_py.errors import VmError
except Exception:  # pragma: no cover - bootstrap path
    class VmError(Exception):  # type: ignore
        """Fallback error used when vm_py.errors isn't available yet."""
        pass


_ANIMICA_PREFIX = b"\x19animica:"  # common DS domain prefix


def _ensure_bytes(buf: object, name: str) -> bytes:
    if isinstance(buf, (bytes, bytearray, memoryview)):
        return bytes(buf)
    raise VmError(f"{name} must be bytes-like (got {type(buf).__name__})")


def _apply_domain(h, domain: bytes) -> None:
    if domain:
        h.update(_ANIMICA_PREFIX)
        h.update(domain)
        h.update(b"\x00")


# ------------------------------ Keccak Support ------------------------------- #

# Try to provide Keccak-256 from common libraries (optional).
# We attempt several possible providers and set flags for availability.
_has_pysha3 = False
_has_pycryptodome = False
try:  # pysha3 (a.k.a. "sha3" package) provides keccak_256 compatible with Ethereum.
    import sha3  # type: ignore
    _has_pysha3 = True
except Exception:
    # Try PyCryptodome (`pip install pycryptodome`) which may expose either
    # `Crypto` or `Cryptodome` top-level packages depending on how it was
    # installed (pycryptodome vs pycryptodomex). Try both import paths.
    try:
        from Crypto.Hash import keccak as _keccak  # type: ignore
        _has_pycryptodome = True
    except Exception:
        try:
            from Cryptodome.Hash import keccak as _keccak  # type: ignore
            _has_pycryptodome = True
        except Exception:
            pass


def _new_keccak256():
    if _has_pysha3:
        return sha3.keccak_256()
    if _has_pycryptodome:
        return _keccak.new(digest_bits=256)  # type: ignore[name-defined]
    raise VmError(
        "keccak256 is unavailable: install either 'pysha3' (preferred), 'pycryptodome', or 'pycryptodomex'."
    )


# ------------------------------- Hash Functions ------------------------------ #

def sha3_256(data: bytes | bytearray | memoryview, *, domain: bytes = b"") -> bytes:
    d = _ensure_bytes(data, "data")
    dom = _ensure_bytes(domain, "domain")
    h = hashlib.sha3_256()
    _apply_domain(h, dom)
    h.update(d)
    return h.digest()


def sha3_512(data: bytes | bytearray | memoryview, *, domain: bytes = b"") -> bytes:
    d = _ensure_bytes(data, "data")
    dom = _ensure_bytes(domain, "domain")
    h = hashlib.sha3_512()
    _apply_domain(h, dom)
    h.update(d)
    return h.digest()


def keccak256(data: bytes | bytearray | memoryview, *, domain: bytes = b"") -> bytes:
    """
    Keccak-256 (pre-SHA3) as used by Ethereum and many ecosystems.
    Requires 'pysha3' or 'pycryptodome'. Raises VmError if not available.
    """
    d = _ensure_bytes(data, "data")
    dom = _ensure_bytes(domain, "domain")
    h = _new_keccak256()
    _apply_domain(h, dom)
    h.update(d)
    # pycryptodome objects sometimes lack hexdigest on older versions; digest() is standard.
    return h.digest()


# Hex helpers (small convenience for tooling/tests)

def sha3_256_hex(data: bytes | bytearray | memoryview, *, domain: bytes = b"") -> str:
    return sha3_256(data, domain=domain).hex()


def sha3_512_hex(data: bytes | bytearray | memoryview, *, domain: bytes = b"") -> str:
    return sha3_512(data, domain=domain).hex()


def keccak256_hex(data: bytes | bytearray | memoryview, *, domain: bytes = b"") -> str:
    return keccak256(data, domain=domain).hex()


# Concatenate multiple chunks deterministically (no copies beyond API normalization)

def _hash_concat(chunks: Iterable[bytes | bytearray | memoryview], h, domain: bytes) -> bytes:
    _apply_domain(h, domain)
    for i, c in enumerate(chunks):
        h.update(_ensure_bytes(c, f"chunk[{i}]"))
    return h.digest()


def hash_concat_sha3_256(*chunks: bytes | bytearray | memoryview, domain: bytes = b"") -> bytes:
    return _hash_concat(chunks, hashlib.sha3_256(), _ensure_bytes(domain, "domain"))


def hash_concat_sha3_512(*chunks: bytes | bytearray | memoryview, domain: bytes = b"") -> bytes:
    return _hash_concat(chunks, hashlib.sha3_512(), _ensure_bytes(domain, "domain"))


def hash_concat_keccak256(*chunks: bytes | bytearray | memoryview, domain: bytes = b"") -> bytes:
    h = _new_keccak256()
    return _hash_concat(chunks, h, _ensure_bytes(domain, "domain"))


# ----------------------------- Streaming Interface --------------------------- #

@runtime_checkable
class _HasherLike(Protocol):
    def update(self, data: bytes) -> None: ...
    def digest(self) -> bytes: ...
    def hexdigest(self) -> str: ...


class _BaseStreamHasher:
    """Shared utilities for streaming hashers (enforces bytes-only I/O)."""

    __slots__ = ("_h", "_finalized")

    def __init__(self, h: _HasherLike, domain: bytes = b"") -> None:
        self._h = h
        self._finalized = False
        _apply_domain(self._h, _ensure_bytes(domain, "domain"))

    def update(self, chunk: bytes | bytearray | memoryview) -> None:
        if self._finalized:
            raise VmError("hasher already finalized")
        self._h.update(_ensure_bytes(chunk, "chunk"))

    def digest(self) -> bytes:
        self._finalized = True
        # Some backends do not support copy(); we favor a one-shot finalize for determinism.
        return self._h.digest()

    def hexdigest(self) -> str:
        self._finalized = True
        try:
            return self._h.hexdigest()  # type: ignore[attr-defined]
        except Exception:
            return self._h.digest().hex()


class Sha3_256(_BaseStreamHasher):
    def __init__(self, *, domain: bytes = b"") -> None:
        super().__init__(hashlib.sha3_256(), domain=domain)


class Sha3_512(_BaseStreamHasher):
    def __init__(self, *, domain: bytes = b"") -> None:
        super().__init__(hashlib.sha3_512(), domain=domain)


class Keccak256(_BaseStreamHasher):
    def __init__(self, *, domain: bytes = b"") -> None:
        super().__init__(_new_keccak256(), domain=domain)


# ------------------------------- Public Exports ------------------------------ #

HAS_KECCAK = _has_pysha3 or _has_pycryptodome

__all__ = [
    "sha3_256",
    "sha3_512",
    "keccak256",
    "sha3_256_hex",
    "sha3_512_hex",
    "keccak256_hex",
    "hash_concat_sha3_256",
    "hash_concat_sha3_512",
    "hash_concat_keccak256",
    "Sha3_256",
    "Sha3_512",
    "Keccak256",
    "HAS_KECCAK",
]
