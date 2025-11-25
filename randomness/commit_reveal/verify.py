# Copyright (c) Animica.
# SPDX-License-Identifier: MIT
"""
Verify that a reveal (addr, salt, payload[, domain_tag]) matches a prior commitment.

Definition
----------
Given a prior commitment C and a reveal tuple (addr, salt, payload[, domain]),
we recompute:

    C' = H(domain || addr || salt || payload)

and check C' == C using a constant-time comparison.

This module exposes:
- `verify_reveal(...)` : returns True on success, raises BadReveal on mismatch.
- `verify_reveal_hex(...)` : convenience wrapper accepting hex commitment.
- `normalize_commitment(...)` : helper to turn hex/bytes into 32 bytes.

Inputs must be bytes; `verify_reveal_hex` exists for hex commitment.
"""

from __future__ import annotations

import binascii
import hmac
from dataclasses import dataclass
from typing import Optional, Union

from randomness.errors import BadReveal
from randomness.commit_reveal.commit import build_commitment

BytesLike = Union[bytes, bytearray, memoryview]


def _as_bytes(x: BytesLike) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    raise TypeError("expected bytes-like object")


def _from_hex(s: str) -> bytes:
    s = s.lower()
    if s.startswith("0x"):
        s = s[2:]
    try:
        return binascii.unhexlify(s)
    except (binascii.Error, ValueError) as e:
        raise ValueError("invalid hex string") from e


def normalize_commitment(commitment: Union[BytesLike, str]) -> bytes:
    """
    Normalize a commitment into 32 raw bytes.

    Accepts:
      - bytes/bytearray/memoryview (must be 32 bytes)
      - hex string with/without 0x prefix (must decode to 32 bytes)
    """
    if isinstance(commitment, str):
        c = _from_hex(commitment)
    else:
        c = _as_bytes(commitment)

    if len(c) != 32:
        raise ValueError("commitment must be exactly 32 bytes")
    return c


@dataclass(frozen=True, slots=True)
class Reveal:
    """Container for reveal inputs."""
    addr: bytes
    salt: bytes
    payload: bytes = b""
    domain_tag: Optional[bytes] = None


def verify_reveal(
    commitment: Union[BytesLike, str],
    *,
    addr: BytesLike,
    salt: BytesLike,
    payload: BytesLike = b"",
    domain_tag: Optional[BytesLike] = None,
    raise_on_fail: bool = True,
) -> bool:
    """
    Verify a reveal against a prior commitment.

    Parameters
    ----------
    commitment : bytes | hex str
        Prior commitment (32 bytes). Hex with/without 0x is accepted.
    addr : bytes-like
        Committer's canonical address bytes.
    salt : bytes-like
        Secret salt revealed alongside payload.
    payload : bytes-like, optional
        Optional payload bound in the commitment (default: b"").
    domain_tag : bytes-like, optional
        Domain separation tag; if None, the default tag from `build_commitment` is used.
    raise_on_fail : bool
        If True, raise BadReveal on mismatch; otherwise return False.

    Returns
    -------
    bool
        True if the reveal matches the commitment; False only if raise_on_fail=False and mismatch.

    Raises
    ------
    BadReveal
        If the recomputed commitment does not match the provided one (and raise_on_fail=True).
    TypeError / ValueError
        If inputs are malformed (propagated from validation).
    """
    c_given = normalize_commitment(commitment)
    addr_b = _as_bytes(addr)
    salt_b = _as_bytes(salt)
    payload_b = _as_bytes(payload)
    tag_b = None if domain_tag is None else _as_bytes(domain_tag)

    c_expected = build_commitment(addr_b, salt_b, payload_b, domain_tag=tag_b)

    ok = hmac.compare_digest(c_expected, c_given)
    if ok:
        return True

    if raise_on_fail:
        raise BadReveal("reveal does not match prior commitment")
    return False


def verify_reveal_hex(
    commitment_hex: str,
    *,
    addr: BytesLike,
    salt: BytesLike,
    payload: BytesLike = b"",
    domain_tag: Optional[BytesLike] = None,
    raise_on_fail: bool = True,
) -> bool:
    """Hex-friendly wrapper around `verify_reveal`."""
    return verify_reveal(
        commitment_hex,
        addr=addr,
        salt=salt,
        payload=payload,
        domain_tag=domain_tag,
        raise_on_fail=raise_on_fail,
    )


__all__ = [
    "Reveal",
    "normalize_commitment",
    "verify_reveal",
    "verify_reveal_hex",
]
