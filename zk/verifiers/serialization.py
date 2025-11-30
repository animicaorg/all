"""
zk.verifiers.serialization
==========================

Canonical encoding/decoding utilities for verification envelopes and common
hex/bytes helpers used across ZK protocol adapters.

Goals
-----
1) Provide **stable, deterministic JSON** encoding suitable for hashing,
   caching, and signature checks.
2) Make hex/bytes handling **ergonomic and explicit**:
   - Accept bytes-like values and hex strings with/without "0x".
   - Emit lowercase hex with a "0x" prefix by default.
   - Optionally pad odd-length hex nibbles (off by default; we prefer strict).
3) Offer a small set of **shape checks** for Animica verification envelopes.

Envelope shape reminder
-----------------------
An *Animica verification envelope* is a mapping with (at least) these keys:
{
  "scheme": { "protocol": "groth16" | "plonk_kzg" | "stark", ... },
  "proof":  { ... },          # protocol-specific JSON
  "public": [ ... ] | { ... },# public inputs / signals
  "vk":     { ... }           # verifying key JSON
}

This module does not validate protocol-specific content—only presence and basic
types—leaving strict checks to the concrete verifier backends.

Public API
----------
- is_hex_str(s, require_prefix=False, even=True) -> bool
- bytes_to_hex(b, prefix=True, lower=True) -> str
- hex_to_bytes(s, allow_0x=True, pad_odd_nibbles=False) -> bytes
- normalize_hex_str(s, prefix=True, lower=True, even=True, pad_odd_nibbles=False) -> str
- to_bytes(obj) -> bytes
- canonicalize(obj, *, hexify_bytes=True, normalize_hex_strings=True) -> Any
- dumps_canonical(obj) -> str
- dumps_canonical_bytes(obj) -> bytes
- assert_envelope_shape(envelope) -> None
- canonical_envelope(envelope) -> dict

All functions are pure and side-effect free.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Iterable, Union, overload

try:
    # Prefer a fast JSON encoder if present.
    import orjson as _fastjson  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _fastjson = None  # type: ignore

from . import ZKError

_HEX_RE = re.compile(r"^(0x)?[0-9a-fA-F]*$")


def is_hex_str(s: str, *, require_prefix: bool = False, even: bool = True) -> bool:
    """
    Return True if `s` looks like a (possibly 0x-prefixed) hex string.

    Parameters
    ----------
    s : str
        Candidate string.
    require_prefix : bool
        If True, require the '0x' prefix to be present.
    even : bool
        If True, require an even number of hex nibbles after optional prefix.

    Notes
    -----
    This performs only lexical checks; it does not parse into bytes.
    """
    if not isinstance(s, str):
        return False
    if not _HEX_RE.match(s):
        return False
    has_prefix = s.startswith(("0x", "0X"))
    if require_prefix and not has_prefix:
        return False
    # Length of hex nibbles (strip optional 0x)
    hex_part = s[2:] if has_prefix else s
    if even and (len(hex_part) % 2 != 0):
        return False
    return True


def bytes_to_hex(
    b: Union[bytes, bytearray, memoryview], *, prefix: bool = True, lower: bool = True
) -> str:
    """
    Encode bytes-like into a hex string.

    Returns lowercase hex with '0x' prefix by default.
    """
    if isinstance(b, memoryview):
        b = b.tobytes()
    if not isinstance(b, (bytes, bytearray)):
        raise TypeError(f"bytes_to_hex: expected bytes-like, got {type(b).__name__}")
    h = b.hex()
    if not lower:
        h = h.upper()
    return ("0x" + h) if prefix else h


def hex_to_bytes(
    s: str,
    *,
    allow_0x: bool = True,
    pad_odd_nibbles: bool = False,
) -> bytes:
    """
    Decode a hex string into bytes.

    Parameters
    ----------
    s : str
        Hex string, optionally '0x'-prefixed if allow_0x=True.
    allow_0x : bool
        If False, disallow the '0x' prefix.
    pad_odd_nibbles : bool
        If True and hex nibble count is odd, left-pad with a '0' nibble.
        If False (default), odd nibble count raises ValueError.

    Raises
    ------
    ValueError if input is not valid hex per the constraints.
    """
    if not isinstance(s, str):
        raise TypeError(f"hex_to_bytes: expected str, got {type(s).__name__}")
    if not _HEX_RE.match(s):
        raise ValueError("hex_to_bytes: non-hex characters present")
    has_prefix = s.startswith(("0x", "0X"))
    if has_prefix and not allow_0x:
        raise ValueError("hex_to_bytes: '0x' prefix not allowed")
    hex_part = s[2:] if has_prefix else s
    if len(hex_part) % 2 != 0:
        if pad_odd_nibbles:
            hex_part = "0" + hex_part
        else:
            raise ValueError("hex_to_bytes: odd number of hex nibbles")
    return bytes.fromhex(hex_part)


def normalize_hex_str(
    s: str,
    *,
    prefix: bool = True,
    lower: bool = True,
    even: bool = True,
    pad_odd_nibbles: bool = False,
) -> str:
    """
    Normalize lexical form of a hex string.

    - Enforces/strips '0x' prefix.
    - Adjusts case.
    - Optionally enforces even nibble count (default: True).

    If `even=True` and nibble count is odd, behavior follows `pad_odd_nibbles`.
    """
    if not _HEX_RE.match(s):
        raise ValueError("normalize_hex_str: not a hex string")
    has_prefix = s.startswith(("0x", "0X"))
    hex_part = s[2:] if has_prefix else s
    if even and (len(hex_part) % 2 != 0):
        if pad_odd_nibbles:
            hex_part = "0" + hex_part
        else:
            raise ValueError("normalize_hex_str: odd nibble count")
    if lower:
        hex_part = hex_part.lower()
    else:
        hex_part = hex_part.upper()
    return ("0x" + hex_part) if prefix else hex_part


@overload
def to_bytes(obj: bytes | bytearray | memoryview) -> bytes: ...
@overload
def to_bytes(obj: str) -> bytes: ...
@overload
def to_bytes(obj: Iterable[int]) -> bytes: ...


def to_bytes(obj: Any) -> bytes:
    """
    Convert common byte-like inputs into raw bytes.

    Accepts:
    - bytes / bytearray / memoryview
    - hex strings (with/without '0x', even number of nibbles)
    - iterable of ints (0..255)

    Raises
    ------
    TypeError / ValueError on unsupported or invalid input.
    """
    if isinstance(obj, bytes):
        return obj
    if isinstance(obj, bytearray):
        return bytes(obj)
    if isinstance(obj, memoryview):
        return obj.tobytes()
    if isinstance(obj, str):
        if not is_hex_str(obj, require_prefix=False, even=True):
            raise ValueError("to_bytes: string input must be hex (even-length)")
        return hex_to_bytes(obj)
    # Fall back to iterable of ints
    try:
        return bytes(obj)  # type: ignore[arg-type]
    except Exception as e:  # pragma: no cover - defensive
        raise TypeError(f"to_bytes: unsupported input type {type(obj).__name__}") from e


def _canonicalize_mapping(d: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return a new dict with keys sorted (lexicographic) and values canonicalized.
    """
    items = sorted(d.items(), key=lambda kv: kv[0])
    return {k: canonicalize(v) for k, v in items}


def canonicalize(
    obj: Any,
    *,
    hexify_bytes: bool = True,
    normalize_hex_strings: bool = True,
) -> Any:
    """
    Recursively transform an object into a canonical, JSON-safe structure.

    Rules
    -----
    - bytes/bytearray/memoryview -> hex string (lowercase, '0x' prefix) if hexify_bytes=True,
      else convert to a list[int].
    - str that *lexically* looks like hex -> normalized per normalize_hex_str()
      if normalize_hex_strings=True. Otherwise left as-is.
    - int/float/bool/None -> unchanged.
    - list/tuple -> list of canonicalized elements.
    - mapping -> dict with **sorted keys** and canonicalized values.
    - other types -> attempt to use __iter__ to list[int] if sensible; otherwise
      raise TypeError.

    Notes
    -----
    Canonicalization does *not* coerce ints into hex strings; that decision
    belongs to the protocol-specific encoders (some schemes want decimal ints).
    """
    # bytes-like
    if isinstance(obj, (bytes, bytearray, memoryview)):
        if hexify_bytes:
            return bytes_to_hex(obj)
        return list(bytes(obj))

    # strings (potentially hex)
    if isinstance(obj, str):
        if normalize_hex_strings and _HEX_RE.match(obj):
            return normalize_hex_str(obj)
        return obj

    # scalars
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj

    # list/tuple
    if isinstance(obj, (list, tuple)):
        return [
            canonicalize(
                x,
                hexify_bytes=hexify_bytes,
                normalize_hex_strings=normalize_hex_strings,
            )
            for x in obj
        ]

    # mappings (dict-like)
    if isinstance(obj, Mapping):
        return _canonicalize_mapping(obj)

    # iterables of ints, last resort
    try:
        return [int(x) for x in obj]  # type: ignore[assignment]
    except Exception as e:
        raise TypeError(f"canonicalize: unsupported type {type(obj).__name__}") from e


def dumps_canonical(obj: Any) -> str:
    """
    Dump object to a canonical JSON string:
    - keys sorted
    - no whitespace padding (',' and ':')
    - ensure_ascii=False (UTF-8)
    - bytes and hex-strings normalized via canonicalize()
    """
    canon = canonicalize(obj)
    if _fastjson is not None:  # pragma: no cover - optional fast path
        # orjson.dumps already sorts keys if option is provided; emulate via default since we pre-sorted.
        return (
            _fastjson.dumps(canon, option=_fastjson.OPT_APPEND_NEWLINE)
            .decode("utf-8")
            .rstrip("\n")
        )
    return json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dumps_canonical_bytes(obj: Any) -> bytes:
    """
    Like dumps_canonical(), but returns UTF-8 encoded bytes.
    """
    s = dumps_canonical(obj)
    return s.encode("utf-8")


def assert_envelope_shape(envelope: Mapping[str, Any]) -> None:
    """
    Quick structural checks for an Animica verification envelope.

    Raises
    ------
    ZKError with a descriptive message if shape is invalid.
    """
    if not isinstance(envelope, Mapping):
        raise ZKError("envelope must be a mapping")
    for key in ("scheme", "proof", "public", "vk"):
        if key not in envelope:
            raise ZKError(f"envelope missing required key '{key}'")
    scheme = envelope.get("scheme")
    if not isinstance(scheme, Mapping):
        raise ZKError("envelope.scheme must be a mapping")
    protocol = scheme.get("protocol")
    if not isinstance(protocol, str):
        raise ZKError("envelope.scheme.protocol must be a string")
    # public, proof, vk: accept any JSON-compatible container; very light checks:
    if "public" in envelope and not isinstance(envelope["public"], (Mapping, list)):
        raise ZKError("envelope.public must be a list or mapping")
    if "proof" in envelope and not isinstance(envelope["proof"], (Mapping, list)):
        raise ZKError("envelope.proof must be a list or mapping")
    if "vk" in envelope and not isinstance(envelope["vk"], Mapping):
        raise ZKError("envelope.vk must be a mapping")


def canonical_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return a **canonicalized copy** of `envelope` (keys sorted, hex normalized).

    This does *not* modify the original. It performs `assert_envelope_shape`
    and then runs `canonicalize` recursively. The result is suitable for
    stable JSON serialization and hashing.
    """
    assert_envelope_shape(envelope)
    # We canonicalize the top-level mapping with sorted keys and normalized content.
    return canonicalize(envelope)  # type: ignore[return-value]


__all__ = [
    "is_hex_str",
    "bytes_to_hex",
    "hex_to_bytes",
    "normalize_hex_str",
    "to_bytes",
    "canonicalize",
    "dumps_canonical",
    "dumps_canonical_bytes",
    "assert_envelope_shape",
    "canonical_envelope",
]
