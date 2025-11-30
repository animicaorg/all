"""
Deterministic identifiers for artifacts and verification jobs.

We compute SHA3-256 over canonically-encoded parts with clear domain tags:

- ArtifactId (no address):    H("animica/artifact:v1|" || canon(manifest) || 0x00 || code || 0x00 || canon(abi))
- VerifyJobId (with address): H("animica/verify:v1|"  || norm_addr        || 0x00 || canon(manifest) || 0x00 || code || 0x00 || canon(abi))
- AddressId:                   H("animica/address:v1|" || norm_addr)

Conventions:
- Hashes are returned as 0x-prefixed lowercase hex strings.
- Canonical JSON uses UTF-8, sorted keys, and minimal separators to be stable.
- Inputs accept flexible types (dict/str/bytes/Path). See helpers below.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

# Domain tags (future-proofing: changing shape requires a new :vN suffix)
_DOMAIN_ARTIFACT = b"animica/artifact:v1|"
_DOMAIN_VERIFY = b"animica/verify:v1|"
_DOMAIN_ADDRESS = b"animica/address:v1|"

# Disambiguation separator between concatenated fields
_SEP = b"\x00"


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _to_0x(hex_bytes: bytes) -> str:
    return "0x" + hex_bytes.hex()


def _canonical_json_bytes(value: Any) -> bytes:
    """
    Deterministic JSON encoding:
    - UTF-8
    - sort_keys=True
    - minimal separators
    - ensure_ascii=False (to keep UTF-8 bytes intact)
    """
    # json.dumps guarantees stable key order with sort_keys=True.
    s = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return s.encode("utf-8")


BytesLike = Union[bytes, bytearray, memoryview]


def _as_bytes(x: Union[BytesLike, str, Path, Any]) -> bytes:
    """
    Normalize various inputs to raw bytes:
    - bytes/bytearray/memoryview → bytes
    - str → UTF-8 bytes
    - Path → file bytes (read in binary)
    - dict/list/other JSON-serializable → canonical JSON bytes
    """
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        return x.encode("utf-8")
    if isinstance(x, Path):
        return x.read_bytes()
    # Fallback: attempt canonical JSON encoding
    return _canonical_json_bytes(x)


def _normalize_address_bytes(addr: str | bytes) -> bytes:
    """
    Normalize an Animica address string (bech32m 'anim1...' or hex) to bytes
    for hashing purposes. We treat the address as case-insensitive text and
    hash the normalized ASCII form directly; decoding to binary is deferred
    to the dedicated address validators elsewhere (adapters/pq_addr.py).

    Rules:
    - bytes → used as-is
    - str  → strip whitespace, lower-case, encode UTF-8
    """
    if isinstance(addr, bytes):
        return addr
    return addr.strip().lower().encode("utf-8")


def content_hash_sha3_256(data: BytesLike) -> str:
    """
    Convenience: 0x-prefixed SHA3-256 of raw bytes.
    """
    return _to_0x(_sha3_256(bytes(data)))


def artifact_id(manifest: Any, code: Union[BytesLike, Path], abi: Any) -> str:
    """
    Deterministic id for a (manifest, code, abi) bundle (no address binding).

    Parameters
    ----------
    manifest : Any
        JSON-serializable manifest object (or str/bytes already canonicalized).
    code : bytes|Path
        Compiled contract code as raw bytes (or a Path to the code file).
    abi : Any
        JSON-serializable ABI (or str/bytes).

    Returns
    -------
    str
        0x-prefixed SHA3-256 hex string.
    """
    manifest_b = _as_bytes(manifest)
    code_b = _as_bytes(code)
    abi_b = _as_bytes(abi)

    h = _sha3_256(_DOMAIN_ARTIFACT + manifest_b + _SEP + code_b + _SEP + abi_b)
    return _to_0x(h)


def verify_job_id(
    address: str | bytes, manifest: Any, code: Union[BytesLike, Path], abi: Any
) -> str:
    """
    Deterministic id for a source verification job bound to a specific address.

    This binds the same (manifest, code, abi) uniquely per address to avoid
    collisions across deployments.

    Returns
    -------
    str
        0x-prefixed SHA3-256 hex string.
    """
    addr_b = _normalize_address_bytes(address)
    manifest_b = _as_bytes(manifest)
    code_b = _as_bytes(code)
    abi_b = _as_bytes(abi)

    h = _sha3_256(
        _DOMAIN_VERIFY + addr_b + _SEP + manifest_b + _SEP + code_b + _SEP + abi_b
    )
    return _to_0x(h)


def address_id(address: str | bytes) -> str:
    """
    Stable id for an address string. Useful for table keys, caching, etc.
    """
    addr_b = _normalize_address_bytes(address)
    return _to_0x(_sha3_256(_DOMAIN_ADDRESS + addr_b))


# Optional structured return for clarity where needed
@dataclass(frozen=True)
class BundleIds:
    artifact: str
    verify_job: str
    address: str


def bundle_ids(
    address: str | bytes, manifest: Any, code: Union[BytesLike, Path], abi: Any
) -> BundleIds:
    """
    Convenience to compute all 3 ids consistently.
    """
    return BundleIds(
        artifact=artifact_id(manifest, code, abi),
        verify_job=verify_job_id(address, manifest, code, abi),
        address=address_id(address),
    )


__all__ = [
    "content_hash_sha3_256",
    "artifact_id",
    "verify_job_id",
    "address_id",
    "bundle_ids",
    "BundleIds",
]
