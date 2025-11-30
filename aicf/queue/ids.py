from __future__ import annotations

"""
Deterministic job-id derivation (mirrors capabilities/jobs/id.py).

Definition
----------
job_id = SHA3-256(
    domain("animica/task-id/v1") ||
    u64_be(chain_id) ||
    u64_be(height)   ||
    len(tx_hash)||tx_hash ||
    len(caller)||caller   ||
    len(payload)||payload
)

Notes
-----
- Domain-separated so ids are unambiguous and stable across subsystems.
- All variable-length fields are length-prefixed (big-endian u32) to avoid
  concatenation ambiguity.
- tx_hash/caller inputs accept bytes or hex strings (with or without 0x).
- Returns lowercase hex with 0x prefix for convenience.

If you update the domain tag or layout here, you MUST make the same change in
capabilities/jobs/id.py to keep cross-module ids identical.
"""

import hashlib
import struct
from typing import Union

BytesLike = Union[bytes, bytearray, memoryview, str]

DOMAIN_TAG = b"animica/task-id/v1"  # MUST match capabilities/jobs/id.py


def _as_bytes(x: BytesLike) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        s = x.strip()
        if s.startswith(("0x", "0X")):
            s = s[2:]
            # If odd-length, left-pad a zero nibble
            if len(s) % 2 == 1:
                s = "0" + s
            try:
                return bytes.fromhex(s)
            except ValueError as e:
                raise ValueError(f"invalid hex string: {x!r}") from e
        return s.encode("utf-8")
    raise TypeError(f"unsupported input type: {type(x).__name__}")


def _len_prefix(b: bytes) -> bytes:
    return struct.pack(">I", len(b))


def derive_job_id_bytes(
    chain_id: int,
    height: int,
    tx_hash: BytesLike,
    caller: BytesLike,
    payload: BytesLike,
    *,
    domain: bytes = DOMAIN_TAG,
) -> bytes:
    """
    Compute deterministic job-id bytes.

    Args:
        chain_id: Network/chain identifier (int, will be encoded u64 big-endian).
        height:   Block height at which the request is made (u64 big-endian).
        tx_hash:  Transaction hash (bytes or hex string).
        caller:   Caller/account/address (bytes or hex string).
        payload:  Serialized job payload (CBOR/JSON/etc) as bytes.
        domain:   Domain tag for separation (default DOMAIN_TAG).

    Returns:
        32-byte SHA3-256 digest.
    """
    if chain_id < 0 or height < 0:
        raise ValueError("chain_id and height must be non-negative integers")

    tx_b = _as_bytes(tx_hash)
    caller_b = _as_bytes(caller)
    payload_b = _as_bytes(payload)

    buf = bytearray()
    buf += domain + b"\x00"
    buf += struct.pack(">Q", int(chain_id))
    buf += struct.pack(">Q", int(height))
    buf += _len_prefix(tx_b) + tx_b
    buf += _len_prefix(caller_b) + caller_b
    buf += _len_prefix(payload_b) + payload_b

    return hashlib.sha3_256(buf).digest()


def derive_job_id(
    chain_id: int,
    height: int,
    tx_hash: BytesLike,
    caller: BytesLike,
    payload: BytesLike,
    *,
    domain: bytes = DOMAIN_TAG,
    with_prefix: bool = True,
) -> str:
    """
    Compute deterministic job-id as a hex string.

    Returns:
        '0x' + 64-hex digest by default; set with_prefix=False for bare hex.
    """
    digest = derive_job_id_bytes(
        chain_id=chain_id,
        height=height,
        tx_hash=tx_hash,
        caller=caller,
        payload=payload,
        domain=domain,
    )
    hx = digest.hex()
    return "0x" + hx if with_prefix else hx


__all__ = [
    "DOMAIN_TAG",
    "derive_job_id",
    "derive_job_id_bytes",
]
