from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from .bytes import BytesLike, ensure_bytes, to_hex


@runtime_checkable
class _HashLike(Protocol):
    def update(self, data: bytes) -> None: ...
    def digest(self) -> bytes: ...
    def hexdigest(self) -> str: ...
    def copy(self) -> "_HashLike": ...


# --- Keccak-256 (Ethereum-style) ----------------------------------------------
# CPython's hashlib exposes NIST SHA3 (sha3_256/sha3_512) but NOT always the
# original Keccak-256 padding. We try hashlib.new("keccak256") first (some
# platforms/wheels provide it). If unavailable, we fall back to the `pysha3`
# package which exposes `sha3.keccak_256()`.

def _new_keccak256() -> _HashLike:
    # Try native hashlib provider (present on some platforms)
    if "keccak256" in hashlib.algorithms_available:
        return hashlib.new("keccak256")  # type: ignore[arg-type]
    # Fallback: pysha3
    try:
        import sha3  # type: ignore
    except Exception as e:  # pragma: no cover - import path error
        raise RuntimeError(
            "keccak256 not available. Install the 'pysha3' package: "
            "pip install pysha3"
        ) from e
    return sha3.keccak_256()  # type: ignore[attr-defined]


def keccak256(data: BytesLike) -> bytes:
    """Return Keccak-256 digest of *data* (bytes)."""
    h = _new_keccak256()
    h.update(ensure_bytes(data))
    return h.digest()


def keccak256_hex(data: BytesLike, *, prefix: bool = True) -> str:
    """Return hex string of Keccak-256 digest (0x-prefixed by default)."""
    return to_hex(keccak256(data), prefix=prefix)


class Keccak256:
    """Streaming Keccak-256 hasher with update()/digest()/hexdigest()."""

    __slots__ = ("_h",)

    def __init__(self) -> None:
        self._h = _new_keccak256()

    def update(self, data: BytesLike) -> "Keccak256":
        self._h.update(ensure_bytes(data))
        return self

    def digest(self) -> bytes:
        return self._h.digest()

    def hexdigest(self, *, prefix: bool = True) -> str:
        return to_hex(self._h.digest(), prefix=prefix)

    def copy(self) -> "Keccak256":
        c = object.__new__(Keccak256)
        c._h = self._h.copy()
        return c


# --- NIST SHA3 (FIPS-202) -----------------------------------------------------
# These are always available via hashlib on Python 3.6+.

def sha3_256(data: BytesLike) -> bytes:
    """Return SHA3-256 digest of *data* (NIST version, not Keccak padding)."""
    h = hashlib.sha3_256()
    h.update(ensure_bytes(data))
    return h.digest()


def sha3_256_hex(data: BytesLike, *, prefix: bool = True) -> str:
    return to_hex(sha3_256(data), prefix=prefix)


def sha3_512(data: BytesLike) -> bytes:
    """Return SHA3-512 digest of *data* (NIST version)."""
    h = hashlib.sha3_512()
    h.update(ensure_bytes(data))
    return h.digest()


def sha3_512_hex(data: BytesLike, *, prefix: bool = True) -> str:
    return to_hex(sha3_512(data), prefix=prefix)


class SHA3_256:
    """Streaming SHA3-256 hasher."""

    __slots__ = ("_h",)

    def __init__(self) -> None:
        self._h = hashlib.sha3_256()

    def update(self, data: BytesLike) -> "SHA3_256":
        self._h.update(ensure_bytes(data))
        return self

    def digest(self) -> bytes:
        return self._h.digest()

    def hexdigest(self, *, prefix: bool = True) -> str:
        return to_hex(self._h.digest(), prefix=prefix)

    def copy(self) -> "SHA3_256":
        c = object.__new__(SHA3_256)
        c._h = self._h.copy()
        return c


class SHA3_512:
    """Streaming SHA3-512 hasher."""

    __slots__ = ("_h",)

    def __init__(self) -> None:
        self._h = hashlib.sha3_512()

    def update(self, data: BytesLike) -> "SHA3_512":
        self._h.update(ensure_bytes(data))
        return self

    def digest(self) -> bytes:
        return self._h.digest()

    def hexdigest(self, *, prefix: bool = True) -> str:
        return to_hex(self._h.digest(), prefix=prefix)

    def copy(self) -> "SHA3_512":
        c = object.__new__(SHA3_512)
        c._h = self._h.copy()
        return c


__all__ = [
    "keccak256",
    "keccak256_hex",
    "Keccak256",
    "sha3_256",
    "sha3_256_hex",
    "SHA3_256",
    "sha3_512",
    "sha3_512_hex",
    "SHA3_512",
]
