# Copyright (c) Animica.
# SPDX-License-Identifier: MIT
"""
randomness.utils.bytes
======================

Small utilities for working with hex/bytes plus strict **length guards**.
Kept dependency-free (stdlib only) and safe for deterministic contexts.

Highlights
----------
- :func:`to_hex` / :func:`from_hex` with strict validation.
- :func:`as_bytes` to normalize bytes-like values.
- Length guards: :func:`ensure_len`, :func:`ensure_max_len`,
  :func:`ensure_min_len`, :func:`ensure_len_range`.
- Padding helpers: :func:`left_pad`, :func:`right_pad`.
- :func:`consteq` timing-safe equality (hmac.compare_digest).

These helpers are intentionally strict to prevent ambiguous encodings and
footguns in commitment / transcript derivations.
"""

from __future__ import annotations

import hmac
import re
from typing import Union

BytesLike = Union[bytes, bytearray, memoryview]

__all__ = [
    "to_hex",
    "from_hex",
    "is_hex",
    "as_bytes",
    "ensure_len",
    "ensure_max_len",
    "ensure_min_len",
    "ensure_len_range",
    "left_pad",
    "right_pad",
    "consteq",
]

# -----------------
# Hex <-> Bytes I/O
# -----------------

_HEX_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]*$")


def is_hex(s: str) -> bool:
    """
    Return True if *s* is a valid hex string with an optional ``0x`` prefix.
    Enforces no whitespace and even number of nibbles (after optional prefix).
    """
    if not isinstance(s, str):
        return False
    if not _HEX_RE.match(s):
        return False
    body = s[2:] if s.startswith(("0x", "0X")) else s
    return len(body) % 2 == 0


def from_hex(s: str) -> bytes:
    """
    Convert a hex string (with optional ``0x``) to bytes.

    Strict rules:
    - No whitespace.
    - Only 0-9a-fA-F characters (plus optional prefix).
    - Even-length nibble count.
    """
    if not isinstance(s, str):
        raise TypeError("from_hex expects a str")
    if not _HEX_RE.match(s):
        raise ValueError("invalid hex string (characters or whitespace)")
    body = s[2:] if s.startswith(("0x", "0X")) else s
    if len(body) % 2 != 0:
        raise ValueError("hex string must have an even number of nibbles")
    try:
        return bytes.fromhex(body)
    except ValueError as e:  # pragma: no cover - defensive
        raise ValueError(f"invalid hex: {e}") from e


def to_hex(b: BytesLike, *, prefix: str = "0x") -> str:
    """
    Encode bytes as lowercase hex. By default returns with ``0x`` prefix.
    """
    bb = as_bytes(b)
    hx = bb.hex()
    return (prefix or "") + hx


# --------------
# Bytes utilities
# --------------


def as_bytes(x: BytesLike) -> bytes:
    """Normalize bytes-like to immutable :class:`bytes`."""
    if isinstance(x, bytes):
        return x
    if isinstance(x, (bytearray, memoryview)):
        return bytes(x)
    raise TypeError(f"expected bytes-like, got {type(x)!r}")


# ----------------
# Length validators
# ----------------


def ensure_len(b: BytesLike, expected: int, *, name: str = "value") -> bytes:
    """
    Ensure ``len(b) == expected``. Returns bytes on success, raises ValueError otherwise.
    """
    bb = as_bytes(b)
    if len(bb) != expected:
        raise ValueError(f"{name} must be {expected} bytes, got {len(bb)}")
    return bb


def ensure_max_len(b: BytesLike, max_len: int, *, name: str = "value") -> bytes:
    """
    Ensure ``len(b) <= max_len``. Returns bytes on success, raises ValueError otherwise.
    """
    bb = as_bytes(b)
    if len(bb) > max_len:
        raise ValueError(f"{name} must be at most {max_len} bytes, got {len(bb)}")
    return bb


def ensure_min_len(b: BytesLike, min_len: int, *, name: str = "value") -> bytes:
    """
    Ensure ``len(b) >= min_len``. Returns bytes on success, raises ValueError otherwise.
    """
    bb = as_bytes(b)
    if len(bb) < min_len:
        raise ValueError(f"{name} must be at least {min_len} bytes, got {len(bb)}")
    return bb


def ensure_len_range(
    b: BytesLike, *, min_len: int, max_len: int, name: str = "value"
) -> bytes:
    """
    Ensure ``min_len <= len(b) <= max_len``. Returns bytes on success.
    """
    if min_len > max_len:
        raise ValueError("min_len cannot exceed max_len")
    bb = as_bytes(b)
    n = len(bb)
    if n < min_len or n > max_len:
        raise ValueError(f"{name} length must be in [{min_len}, {max_len}], got {n}")
    return bb


# -------
# Padding
# -------


def left_pad(b: BytesLike, size: int, fill: int = 0) -> bytes:
    """
    Left-pad to *size* bytes with *fill* (0..255). If already >= size, returns original bytes.
    """
    bb = as_bytes(b)
    if len(bb) >= size:
        return bb
    if not (0 <= fill <= 255):
        raise ValueError("fill must be in 0..255")
    return bytes([fill]) * (size - len(bb)) + bb


def right_pad(b: BytesLike, size: int, fill: int = 0) -> bytes:
    """
    Right-pad to *size* bytes with *fill* (0..255). If already >= size, returns original bytes.
    """
    bb = as_bytes(b)
    if len(bb) >= size:
        return bb
    if not (0 <= fill <= 255):
        raise ValueError("fill must be in 0..255")
    return bb + bytes([fill]) * (size - len(bb))


# ---------------
# Constant-time eq
# ---------------


def consteq(a: BytesLike, b: BytesLike) -> bool:
    """Timing-safe equality for two bytes-like values."""
    return hmac.compare_digest(as_bytes(a), as_bytes(b))
