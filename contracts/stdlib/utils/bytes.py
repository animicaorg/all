# -*- coding: utf-8 -*-
"""
contracts.stdlib.utils.bytes
============================

Small, deterministic helpers for working with hex/bytes/int values in
contract code and ABI argument handling.

Goals:
- A single canonical path to normalize inputs from dapps/SDKs (hex strings,
  raw bytes, or small integers) into **bytes**.
- Round-trip stable encoders/decoders for logs/events and ABI payloads.
- Zero external deps; predictable behavior in the Python-VM.

Conventions:
- Hex strings may start with "0x" (preferred) or be bare; letters are
  canonicalized to lowercase; nibble-length is made **even** by optional
  left-pad with a zero nibble when needed.
- Integers are encoded as **minimal** big-endian unsigned bytes; 0 → b"\x00".
- Booleans for event fields use b"\x01"/b"\x00" (see bool_flag()).
- Addresses/ids are opaque bytes; size validation is handled by callers.

This module is intentionally tiny but opinionated; it avoids anything that
could vary across platforms/locales or depend on ambient encodings.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional, Tuple, Union, overload

# Type aliases
HexStr = str
BytesLike = Union[bytes, bytearray]
IntoBytes = Union[BytesLike, HexStr, int, bool]

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class BytesError(ValueError):
    """Raised on invalid hex/bytes conversions or size violations."""


# ---------------------------------------------------------------------------
# Core predicates and small utilities
# ---------------------------------------------------------------------------

def is_byteslike(v: object) -> bool:
    """Return True if *v* is bytes or bytearray."""
    return isinstance(v, (bytes, bytearray))


def has_0x_prefix(s: str) -> bool:
    """Return True if *s* starts with '0x' or '0X'."""
    return len(s) >= 2 and s[0] == "0" and (s[1] == "x" or s[1] == "X")


def strip_0x(s: str) -> str:
    """Remove a leading 0x/0X from *s* if present."""
    return s[2:] if has_0x_prefix(s) else s


def add_0x(s: str) -> str:
    """Ensure *s* has '0x' prefix (does not alter casing of payload)."""
    return s if has_0x_prefix(s) else "0x" + s


def is_hex_str(s: str, *, allow_prefix: bool = True, even: Optional[bool] = None) -> bool:
    """
    Heuristically validate a hex string.

    Parameters
    ----------
    s : str
        Candidate string.
    allow_prefix : bool
        Whether '0x'/'0X' is allowed (and ignored) during validation.
    even : Optional[bool]
        If True, require even nibble length; if False, require odd length;
        if None, ignore parity.

    Returns
    -------
    bool
    """
    if not isinstance(s, str) or s == "":
        return False
    payload = strip_0x(s) if allow_prefix else s
    # all hex?
    for ch in payload:
        if not ("0" <= ch <= "9" or "a" <= ch.lower() <= "f"):
            return False
    if even is None:
        return True
    return (len(payload) % 2 == 0) if even else (len(payload) % 2 == 1)


# ---------------------------------------------------------------------------
# Hex <-> bytes canonical converters
# ---------------------------------------------------------------------------

def normalize_hex(
    h: HexStr,
    *,
    ensure_even: bool = True,
    with_prefix: bool = True,
    lowercase: bool = True,
) -> HexStr:
    """
    Canonicalize a hex string:

    - Strips optional 0x prefix.
    - Lowercases (unless *lowercase* is False).
    - Ensures even length by left-padding a '0' nibble when *ensure_even* is True.
    - Adds 0x prefix when *with_prefix* is True.

    Raises BytesError on invalid characters.
    """
    if not isinstance(h, str):
        raise BytesError("normalize_hex: expected str")
    core = strip_0x(h)
    if core == "":
        core = ""
    # validate characters
    for ch in core:
        if not ("0" <= ch <= "9" or "a" <= ch.lower() <= "f"):
            raise BytesError(f"normalize_hex: non-hex character {ch!r}")
    if lowercase:
        core = core.lower()
    if ensure_even and (len(core) % 2 == 1):
        core = "0" + core
    return add_0x(core) if with_prefix else core


def hex_to_bytes(h: HexStr, *, allow_odd: bool = True) -> bytes:
    """
    Convert hex string to bytes. Accepts optional 0x prefix.
    If *allow_odd* and length is odd, left-pads a '0' nibble.

    Raises BytesError on invalid input.
    """
    if not isinstance(h, str):
        raise BytesError("hex_to_bytes: expected str")
    core = strip_0x(h).lower()
    if core == "":
        return b""
    # validate & normalize parity
    for ch in core:
        if not ("0" <= ch <= "9" or "a" <= ch <= "f"):
            raise BytesError(f"hex_to_bytes: non-hex character {ch!r}")
    if len(core) % 2 == 1:
        if not allow_odd:
            raise BytesError("hex_to_bytes: odd-length hex not allowed")
        core = "0" + core
    try:
        return bytes.fromhex(core)
    except ValueError as e:
        # Defensive, should be unreachable after our checks.
        raise BytesError(f"hex_to_bytes: {e}") from e


def bytes_to_hex(b: BytesLike, *, prefix: bool = True, lowercase: bool = True) -> HexStr:
    """
    Convert bytes/bytearray to hex string with canonical options.
    """
    if not is_byteslike(b):
        raise BytesError("bytes_to_hex: expected bytes/bytearray")
    s = bytes(b).hex()
    if not lowercase:
        s = s.upper()
    return ("0x" + s) if prefix else s


# ---------------------------------------------------------------------------
# Integer <-> bytes (minimal big-endian, unsigned)
# ---------------------------------------------------------------------------

def int_to_be(n: int, *, min_len: int = 1) -> bytes:
    """
    Encode non-negative integer as **minimal** big-endian bytes.

    - 0 encodes to b"\\x00"
    - Left-pads with zero bytes to satisfy *min_len* (if provided)
    """
    if not isinstance(n, int):
        raise BytesError("int_to_be: expected int")
    if n < 0:
        raise BytesError("int_to_be: negative values not supported")
    if n == 0:
        out = b"\x00"
    else:
        out = n.to_bytes((n.bit_length() + 7) // 8, "big", signed=False)
    if min_len > len(out):
        out = (b"\x00" * (min_len - len(out))) + out
    return out


def be_to_int(b: BytesLike) -> int:
    """Decode big-endian unsigned bytes to int."""
    if not is_byteslike(b):
        raise BytesError("be_to_int: expected bytes/bytearray")
    return int.from_bytes(bytes(b), "big", signed=False)


# ---------------------------------------------------------------------------
# High-level normalizers (primary entrypoints for ABI args)
# ---------------------------------------------------------------------------

def bool_flag(v: bool) -> bytes:
    """Return b'\\x01' for True and b'\\x00' for False."""
    if v is True:
        return b"\x01"
    if v is False:
        return b"\x00"
    raise BytesError("bool_flag: expected bool")


def to_bytes(
    v: IntoBytes,
    *,
    prefer_hex_for_str: bool = True,
    allow_ascii_fallback: bool = True,
) -> bytes:
    """
    Convert *v* into deterministic bytes:

    - bytes/bytearray → returned as bytes
    - int            → minimal big-endian (see int_to_be)
    - bool           → b'\\x01' / b'\\x00'
    - str            → if *prefer_hex_for_str* and looks like hex (with or without 0x),
                       parse as hex; otherwise, if *allow_ascii_fallback*, encode ASCII strictly.

    Notes
    -----
    ASCII fallback is provided for convenience when working with small labels;
    for addresses/ids you should pass bytes or hex explicitly.
    """
    if is_byteslike(v):
        return bytes(v)  # type: ignore[return-value]
    if isinstance(v, bool):
        return bool_flag(v)
    if isinstance(v, int):
        return int_to_be(v)
    if isinstance(v, str):
        if prefer_hex_for_str and is_hex_str(v, allow_prefix=True):
            return hex_to_bytes(v)
        if allow_ascii_fallback:
            try:
                return v.encode("ascii")
            except UnicodeEncodeError as e:
                raise BytesError("to_bytes: non-ASCII string; pass hex or bytes") from e
        raise BytesError("to_bytes: string provided but ASCII fallback disabled")
    raise BytesError(f"to_bytes: unsupported type {type(v).__name__}")


def to_fixed_bytes(
    v: IntoBytes,
    size: int,
    *,
    left_pad: bool = True,
    truncate: bool = False,
) -> bytes:
    """
    Normalize *v* to exactly *size* bytes.

    Behavior
    --------
    - If len <= size: pad with zero bytes (left by default; right if left_pad=False)
    - If len >  size: if *truncate* is True, drop leading (left_pad=True) or trailing
                      bytes to fit; else raise BytesError.

    Use cases: fixed-size fields (e.g., 32-byte role ids, 20/32-byte addresses).
    """
    b = to_bytes(v)
    ln = len(b)
    if ln == size:
        return b
    if ln < size:
        pad = b"\x00" * (size - ln)
        return (pad + b) if left_pad else (b + pad)
    # ln > size
    if not truncate:
        raise BytesError(f"to_fixed_bytes: value length {ln} exceeds required {size}")
    return b[-size:] if left_pad else b[:size]


# ---------------------------------------------------------------------------
# Size guards & composition helpers
# ---------------------------------------------------------------------------

def ensure_len(b: BytesLike, *, min_len: int = 0, max_len: Optional[int] = None) -> None:
    """
    Validate that *b* length is within [min_len, max_len] (if provided).

    Raises BytesError on violation.
    """
    if not is_byteslike(b):
        raise BytesError("ensure_len: expected bytes/bytearray")
    ln = len(b)
    if ln < min_len:
        raise BytesError(f"ensure_len: length {ln} < min_len {min_len}")
    if max_len is not None and ln > max_len:
        raise BytesError(f"ensure_len: length {ln} > max_len {max_len}")


def concat(parts: Iterable[IntoBytes]) -> bytes:
    """Concatenate an iterable of parts after normalizing each to bytes."""
    return b"".join(to_bytes(p) for p in parts)


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "BytesError",
    "HexStr",
    "BytesLike",
    "IntoBytes",
    # predicates
    "is_byteslike",
    "is_hex_str",
    "has_0x_prefix",
    # hex helpers
    "strip_0x",
    "add_0x",
    "normalize_hex",
    "hex_to_bytes",
    "bytes_to_hex",
    # ints
    "int_to_be",
    "be_to_int",
    # high-level
    "bool_flag",
    "to_bytes",
    "to_fixed_bytes",
    # guards & compose
    "ensure_len",
    "concat",
]
