"""
Deterministic hash helpers exposed to contracts.

Surface:
    keccak256(data: bytes) -> bytes
    sha3_256(data: bytes)  -> bytes
    sha3_512(data: bytes)  -> bytes

These delegate to the VM runtime's hash_api to ensure identical behavior
in local simulation and full-node execution. Inputs must be *bytes*.
"""

from __future__ import annotations

from typing import Union

try:
    from vm_py.runtime import hash_api  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("runtime hash_api not available") from e

BytesLike = Union[bytes, bytearray, memoryview]


def _as_bytes(name: str, b: BytesLike) -> bytes:
    if isinstance(b, bytes):
        return b
    if isinstance(b, (bytearray, memoryview)):
        return bytes(b)
    raise TypeError(f"{name} must be bytes-like, got {type(b).__name__}")


def keccak256(data: BytesLike) -> bytes:
    """Return Keccak-256 digest of data as raw bytes (length 32)."""
    d = _as_bytes("data", data)
    out = hash_api.keccak256(d)
    if not isinstance(out, (bytes, bytearray)):
        raise TypeError("hash_api.keccak256 must return bytes")
    return bytes(out)


def sha3_256(data: BytesLike) -> bytes:
    """Return SHA3-256 digest of data as raw bytes (length 32)."""
    d = _as_bytes("data", data)
    out = hash_api.sha3_256(d)
    if not isinstance(out, (bytes, bytearray)):
        raise TypeError("hash_api.sha3_256 must return bytes")
    return bytes(out)


def sha3_512(data: BytesLike) -> bytes:
    """Return SHA3-512 digest of data as raw bytes (length 64)."""
    d = _as_bytes("data", data)
    out = hash_api.sha3_512(d)
    if not isinstance(out, (bytes, bytearray)):
        raise TypeError("hash_api.sha3_512 must return bytes")
    return bytes(out)


__all__ = ("keccak256", "sha3_256", "sha3_512")
