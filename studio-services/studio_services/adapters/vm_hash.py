"""
vm_hash.py
----------

Utilities to compute stable cryptographic identifiers for VM artifacts.

- `code_hash(code: bytes|hexstr) -> str`
    Returns a 0x-prefixed SHA3-256 hash of the compiled code/IR bytes.

- `abi_hash(abi: dict) -> str`, `manifest_hash(manifest: dict) -> str`
    Canonical-JSON → SHA3-256, 0x-prefixed.

- `artifact_digest(code: bytes|hexstr, abi: dict, manifest: dict) -> str`
    Deterministic digest binding code, ABI, and manifest together using
    domain-separated SHA3-256 over their individual hashes.

These helpers are intentionally small and dependency-light so they can be
used by both the verification pipeline and storage layers consistently.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Union

HexStr = str
BytesLike = Union[bytes, bytearray, memoryview, HexStr]


# ----------------------------- helpers ---------------------------------------


def _sha3_256_hex(data: bytes) -> str:
    return "0x" + hashlib.sha3_256(data).hexdigest()


def _as_bytes(b: BytesLike) -> bytes:
    if isinstance(b, (bytes, bytearray, memoryview)):
        return bytes(b)
    if isinstance(b, str):
        s = b.strip()
        if s.startswith(("0x", "0X")):
            try:
                return bytes.fromhex(s[2:])
            except ValueError as e:
                raise ValueError("Invalid hex string for bytes input") from e
        # Treat plain string as text (rare for code blobs, but be tolerant)
        return s.encode("utf-8")
    raise TypeError(f"Unsupported bytes-like type: {type(b)!r}")


def _canon_json_bytes(obj: Any) -> bytes:
    """
    Canonical JSON encoding:
      - UTF-8
      - sorted keys
      - no whitespace (',' ':')
      - ensure_ascii=False so bytes are preserved once encoded
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


# ----------------------------- public API ------------------------------------


def code_hash(code: BytesLike) -> str:
    """
    Compute 0x-prefixed SHA3-256 hash of VM code/IR bytes.

    Accepts raw bytes or 0x-hex string.
    """
    return _sha3_256_hex(_as_bytes(code))


def abi_hash(abi: Dict[str, Any]) -> str:
    """
    Compute 0x-prefixed SHA3-256 of canonical JSON-serialized ABI.
    """
    return _sha3_256_hex(_canon_json_bytes(abi))


def manifest_hash(manifest: Dict[str, Any]) -> str:
    """
    Compute 0x-prefixed SHA3-256 of canonical JSON-serialized manifest.
    """
    return _sha3_256_hex(_canon_json_bytes(manifest))


def artifact_digest(
    code: BytesLike,
    abi: Dict[str, Any],
    manifest: Dict[str, Any],
    *,
    address: Optional[str] = None,
) -> str:
    """
    Compute a deterministic digest binding code+ABI+manifest (and optionally address).

    Domain-separated construction:
        H("animica:artifact" || code_hash || abi_hash || manifest_hash || address?)

    Parameters
    ----------
    code : bytes|0x-hex
        Compiled VM artifact bytes (IR/bytecode).
    abi : dict
        Contract ABI.
    manifest : dict
        Contract manifest (may include ABI; we only bind provided `manifest` object).
    address : optional str
        If provided (0x-hex or bech32 address), it is included verbatim as UTF-8.

    Returns
    -------
    0x-prefixed SHA3-256 digest string.
    """
    ch = code_hash(code).lower()
    ah = abi_hash(abi).lower()
    mh = manifest_hash(manifest).lower()

    domain = b"animica:artifact|v1|"
    parts = [
        domain,
        ch.encode("utf-8"),
        b"|",
        ah.encode("utf-8"),
        b"|",
        mh.encode("utf-8"),
    ]
    if address:
        parts.extend([b"|", address.strip().encode("utf-8")])

    return _sha3_256_hex(b"".join(parts))


__all__ = [
    "code_hash",
    "abi_hash",
    "manifest_hash",
    "artifact_digest",
]
