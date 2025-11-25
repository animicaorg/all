# Copyright (c) Animica.
# SPDX-License-Identifier: MIT
"""
Commitment construction for the randomness beacon's commit–reveal.

Definition
----------
C = H( domain_tag || addr || salt || payload )

- H is SHA3-256.
- `domain_tag` ensures strong domain separation across protocols/usages.
- `addr` is the committer's canonical address *bytes* (already decoded).
- `salt` SHOULD be uniformly random bytes chosen by the committer.
- `payload` is the message being committed to (often empty for pure-beacon).

This module provides a single entrypoint, `build_commitment(...)`, and a
hex-friendly helper `build_commitment_hex(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha3_256
from typing import Optional

# Try to import a canonical domain tag; fall back to a local constant if missing.
try:
    # Expected to be defined in randomness/constants.py
    from randomness.constants import COMMIT_DOMAIN_TAG as _DEFAULT_DOMAIN_TAG  # type: ignore
except Exception:  # pragma: no cover - fallback for early bringup
    _DEFAULT_DOMAIN_TAG = b"animica-rand-commit-v1"

_MIN_SALT_LEN = 8       # bytes; small but non-zero to avoid footguns
_MAX_SALT_LEN = 128     # reasonable guardrail
_MIN_ADDR_LEN = 20      # e.g., 160-bit addresses
_MAX_ADDR_LEN = 64      # supports 512-bit identifiers if needed
_MAX_PAYLOAD_LEN = 1 << 20  # 1 MiB guardrail for commits (not a hard protocol limit)


@dataclass(frozen=True, slots=True)
class CommitInput:
    """Convenience bundle for commit inputs."""
    addr: bytes
    salt: bytes
    payload: bytes = b""
    domain_tag: bytes = _DEFAULT_DOMAIN_TAG


def _validate(addr: bytes, salt: bytes, payload: bytes, domain_tag: bytes) -> None:
    if not isinstance(addr, (bytes, bytearray)):
        raise TypeError("addr must be bytes")
    if not isinstance(salt, (bytes, bytearray)):
        raise TypeError("salt must be bytes")
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")
    if not isinstance(domain_tag, (bytes, bytearray)):
        raise TypeError("domain_tag must be bytes")

    if not (_MIN_ADDR_LEN <= len(addr) <= _MAX_ADDR_LEN):
        raise ValueError(f"addr length must be in [{_MIN_ADDR_LEN}, {_MAX_ADDR_LEN}] bytes")
    if not (_MIN_SALT_LEN <= len(salt) <= _MAX_SALT_LEN):
        raise ValueError(f"salt length must be in [{_MIN_SALT_LEN}, {_MAX_SALT_LEN}] bytes")
    if len(payload) > _MAX_PAYLOAD_LEN:
        raise ValueError(f"payload too large (>{_MAX_PAYLOAD_LEN} bytes)")
    if len(domain_tag) == 0:
        raise ValueError("domain_tag must be non-empty")


def build_commitment(
    addr: bytes,
    salt: bytes,
    payload: bytes = b"",
    *,
    domain_tag: Optional[bytes] = None,
) -> bytes:
    """
    Compute the commitment C = SHA3-256(domain || addr || salt || payload).

    Parameters
    ----------
    addr : bytes
        Canonical address bytes of the committer (e.g., hash of pubkey).
    salt : bytes
        Fresh, uniformly random bytes. Keep secret until reveal.
    payload : bytes, optional
        Extra data to bind in the commitment (default: empty).
    domain_tag : bytes, optional
        Domain separation tag. Defaults to COMMIT_DOMAIN_TAG if available.

    Returns
    -------
    bytes
        32-byte SHA3-256 digest.
    """
    tag = _DEFAULT_DOMAIN_TAG if domain_tag is None else domain_tag
    _validate(addr, salt, payload, tag)

    h = sha3_256()
    h.update(tag)
    h.update(addr)
    h.update(salt)
    h.update(payload)
    return h.digest()


def build_commitment_hex(
    addr: bytes,
    salt: bytes,
    payload: bytes = b"",
    *,
    domain_tag: Optional[bytes] = None,
) -> str:
    """Hex-encoded convenience wrapper for `build_commitment`."""
    return build_commitment(addr, salt, payload, domain_tag=domain_tag).hex()


__all__ = [
    "CommitInput",
    "build_commitment",
    "build_commitment_hex",
]
