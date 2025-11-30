# Copyright (c) Animica.
# SPDX-License-Identifier: MIT
"""
randomness.utils.hash
=====================

Thin SHA3 helpers plus **domain-separated** hashing utilities used by the
randomness beacon. This module intentionally avoids external deps and sticks
to Python's stdlib `hashlib` (SHA3-256/512).

Why domain separation?
----------------------
To keep independent contexts (commit/reveal/VDF/mix/transcripts) from being
accidentally interchangeable or malleable. We prefix all hashes with a static
module prefix and a caller-supplied domain tag, then encode inputs in a
stable, length-delimited TLV format.

Key pieces
----------
- :func:`sha3_256`, :func:`sha3_512`: raw one-shot wrappers.
- :func:`dsha3_256`, :func:`dsha3_512`: domain-separated hash of arbitrary parts.
- :func:`commit`: convenience alias for :func:`dsha3_256`.
- :class:`Transcript`: streaming, domain-separated absorber with labelled fields.

Conventions
-----------
* Domain prefix: ``b"animica|rand|"`` unless overridden by
  ``randomness.constants.DOMAIN_PREFIX``.
* Domain tag: freeform (bytes/str/Enum). Use short, kebab-cased strings like:
  "commit", "reveal", "vdf.input", "vdf.proof", "mix", "transcript".
* Parts are encoded as TLV with per-item type tags to make encodings unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha3_256 as _sha3_256
from hashlib import sha3_512 as _sha3_512
from typing import Any, Iterable, Protocol, Union, overload, runtime_checkable

# Try to import configured prefix (non-fatal fallback).
try:  # pragma: no cover - trivial import guard
    from ..constants import DOMAIN_PREFIX as _CFG_DOMAIN_PREFIX  # type: ignore
except Exception:  # pragma: no cover
    _CFG_DOMAIN_PREFIX = b"animica|rand|"

DomainLike = Union[str, bytes]

__all__ = [
    "sha3_256",
    "sha3_512",
    "dsha3_256",
    "dsha3_512",
    "commit",
    "Transcript",
    "Transcript256",
    "Transcript512",
]

# -------------------------
# Basic one-shot primitives
# -------------------------


def sha3_256(data: bytes) -> bytes:
    """Return SHA3-256(data)."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("sha3_256 expects a bytes-like object")
    return _sha3_256(bytes(data)).digest()


def sha3_512(data: bytes) -> bytes:
    """Return SHA3-512(data)."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("sha3_512 expects a bytes-like object")
    return _sha3_512(bytes(data)).digest()


# --------------------------------
# Stable, self-delimiting encoding
# --------------------------------

# Item type tags (single-byte, stable):
_TT_BYTES = b"\x01"
_TT_STR = b"\x02"
_TT_INT = b"\x03"
_TT_BOOL = b"\x04"
_TT_SEQ = b"\x05"
_TT_NONE = b"\x06"


def _varint_u(n: int) -> bytes:
    """LEB128 unsigned varint."""
    if n < 0:
        raise ValueError("varint only supports non-negative integers")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _int_to_be(n: int) -> bytes:
    if n < 0:
        raise ValueError("only non-negative integers are supported")
    if n == 0:
        return b"\x00"
    length = (n.bit_length() + 7) // 8
    return n.to_bytes(length, "big")


def _as_bytes(x: Any) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, memoryview):
        return bytes(x)
    raise TypeError("expected a bytes-like object")


def _encode_one(x: Any) -> bytes:
    """Encode a single value to a TLV item with a stable, unambiguous format."""
    if x is None:
        return _TT_NONE  # lengthless, constant
    if isinstance(x, (bytes, bytearray, memoryview)):
        b = _as_bytes(x)
        return _TT_BYTES + _varint_u(len(b)) + b
    if isinstance(x, str):
        b = x.encode("utf-8")
        return _TT_STR + _varint_u(len(b)) + b
    if isinstance(x, bool):
        return _TT_BOOL + (b"\x01" if x else b"\x00")
    if isinstance(x, int):
        b = _int_to_be(x)
        return _TT_INT + _varint_u(len(b)) + b
    if isinstance(x, (tuple, list)):
        items = b"".join(_encode_one(v) for v in x)
        return _TT_SEQ + _varint_u(len(items)) + items
    # Fallback: try dataclasses with asdict-like repr? Stay strict to avoid drift.
    raise TypeError(f"unsupported part type: {type(x)!r}")


def _encode_parts(parts: Iterable[Any]) -> bytes:
    enc_items = [_encode_one(p) for p in parts]
    payload = b"".join(enc_items)
    # Envelope with count + payload to make concatenation unambiguous.
    return _varint_u(len(enc_items)) + _varint_u(len(payload)) + payload


def _domain_prefix(domain: DomainLike) -> bytes:
    if hasattr(domain, "value"):  # e.g., Enum
        d = getattr(domain, "value")
        if isinstance(d, bytes):
            tag = d
        else:
            tag = str(d).encode("ascii", "strict")
    elif isinstance(domain, bytes):
        tag = domain
    else:
        tag = str(domain).encode("ascii", "strict")
    # Compose: PREFIX || tag_len || tag || '|'
    return _CFG_DOMAIN_PREFIX + _varint_u(len(tag)) + tag + b"|"


# --------------------------------------
# Domain-separated, one-shot hash helpers
# --------------------------------------


def dsha3_256(domain: DomainLike, *parts: Any) -> bytes:
    """
    Domain-separated SHA3-256 over *parts*.

    Equivalent to:
        SHA3-256( DOMAIN_PREFIX || tag || '|' || ENCODE(parts) )

    Use short, scoped domains, e.g.:
        - "commit"                 (commitments to inputs in the commit phase)
        - "reveal"                 (reveal records)
        - "vdf.input", "vdf.proof" (time-delay inputs/proofs)
        - "mix"                    (final beacon mix)
        - "transcript"             (interactive transcript flows)
    """
    h = _sha3_256()
    h.update(_domain_prefix(domain))
    h.update(_encode_parts(parts))
    return h.digest()


def dsha3_512(domain: DomainLike, *parts: Any) -> bytes:
    """512-bit variant of :func:`dsha3_256`."""
    h = _sha3_512()
    h.update(_domain_prefix(domain))
    h.update(_encode_parts(parts))
    return h.digest()


# Common alias used across the beacon code.
def commit(domain: DomainLike, *parts: Any) -> bytes:
    """Commitment helper (256-bit) — an alias to :func:`dsha3_256`."""
    return dsha3_256(domain, *parts)


# -------------
# Transcripts
# -------------


@dataclass
class _HashFactory:
    kind: str  # "256" or "512"

    def new(self):
        return _sha3_256() if self.kind == "256" else _sha3_512()


class Transcript:
    """
    A small, deterministic transcript (MIH-style sponge wrapper).

    Usage:
        tx = Transcript("transcript")            # choose a clear domain
        tx.absorb("round-id", 42)
        tx.absorb("commitment", b"...")
        dst = tx.challenge("beacon-mix")         # 32-byte challenge
        # or
        out64 = tx.challenge("beacon-mix", out_len=64)

    Notes:
        - Each absorb is TLV-encoded as: label_len || label || ENCODE(values)
        - The transcript is internally seeded with the domain prefix once.
        - `challenge(label, ...)` derives a new digest without mutating the base
          state by copying the hasher, then absorbing a "chal|" sub-domain.
    """

    __slots__ = ("_factory", "_hasher", "_domain")

    def __init__(self, domain: DomainLike, *, bits: int = 256):
        if bits not in (256, 512):
            raise ValueError("bits must be 256 or 512")
        self._factory = _HashFactory("256" if bits == 256 else "512")
        self._hasher = self._factory.new()
        self._domain = _domain_prefix(domain)
        self._hasher.update(self._domain + b"transcript|")  # sub-domain

    def absorb(self, label: str, *values: Any) -> None:
        if not isinstance(label, str):
            raise TypeError("label must be a str")
        lb = label.encode("utf-8")
        self._hasher.update(_varint_u(len(lb)) + lb)
        self._hasher.update(_encode_parts(values))

    def fork(self) -> "Transcript":
        """Create a duplicate transcript with identical state."""
        t = Transcript("fork", bits=256 if self.is_256 else 512)
        # Rebuild with exact bytes by copying the underlying hasher
        t._domain = self._domain
        t._hasher = self._hasher.copy()
        t._factory = self._factory
        return t

    @property
    def is_256(self) -> bool:
        return self._factory.kind == "256"

    def digest(self) -> bytes:
        """Return the current transcript digest (does not mutate the state)."""
        return self._hasher.copy().digest()

    def hexdigest(self) -> str:
        return self._hasher.copy().hexdigest()

    def challenge(self, label: str, *values: Any, out_len: int = 32) -> bytes:
        """
        Derive a challenge from the transcript.

        Produces `out_len` bytes by hashing:
            H( state || "chal|" || label || ENCODE(values) )

        For 256-bit transcripts, `out_len` may be 32 or less.
        For 512-bit transcripts, any `out_len` up to 64 is supported.
        """
        if not (1 <= out_len <= (32 if self.is_256 else 64)):
            raise ValueError("invalid out_len for transcript hash size")
        h = self._hasher.copy()
        h.update(b"chal|")
        lb = label.encode("utf-8")
        h.update(_varint_u(len(lb)) + lb)
        h.update(_encode_parts(values))
        out = h.digest()
        return out[:out_len]


def Transcript256(domain: DomainLike) -> Transcript:
    return Transcript(domain, bits=256)


def Transcript512(domain: DomainLike) -> Transcript:
    return Transcript(domain, bits=512)
