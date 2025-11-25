from __future__ import annotations

"""
stdlib.hash — keccak256 / sha3_256 / sha3_512 wrappers for contracts.

This is a thin, deterministic facade over the simulator runtime hash APIs.
All functions:
  - accept only `bytes`/`bytearray`
  - return raw digest bytes
  - raise ValidationError on bad inputs

Convenience helpers with `*_hex` suffix return lowercase hex strings.
"""

from typing import Any

from ..errors import ValidationError
from ..runtime import hash_api


def _as_bytes(name: str, v: Any) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    raise ValidationError(f"{name} must be bytes")


# ------------ Core digests ------------

def keccak256(data: bytes | bytearray) -> bytes:
    """Return 32-byte Keccak-256 digest."""
    return hash_api.keccak256(_as_bytes("data", data))


def sha3_256(data: bytes | bytearray) -> bytes:
    """Return 32-byte SHA3-256 digest."""
    return hash_api.sha3_256(_as_bytes("data", data))


def sha3_512(data: bytes | bytearray) -> bytes:
    """Return 64-byte SHA3-512 digest."""
    return hash_api.sha3_512(_as_bytes("data", data))


# ------------ Hex helpers ------------

def keccak256_hex(data: bytes | bytearray) -> str:
    """Hex-encoded Keccak-256 digest."""
    return keccak256(data).hex()


def sha3_256_hex(data: bytes | bytearray) -> str:
    """Hex-encoded SHA3-256 digest."""
    return sha3_256(data).hex()


def sha3_512_hex(data: bytes | bytearray) -> str:
    """Hex-encoded SHA3-512 digest."""
    return sha3_512(data).hex()


__all__ = [
    "keccak256",
    "sha3_256",
    "sha3_512",
    "keccak256_hex",
    "sha3_256_hex",
    "sha3_512_hex",
]
