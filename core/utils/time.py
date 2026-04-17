from __future__ import annotations

"""
Timestamp helpers shared across core/rpc paths.

Historical components have produced epoch values in mixed units (seconds,
milliseconds, microseconds, nanoseconds). Consensus data keeps the original
header value, but runtime logic (validation windows, difficulty deltas, RPC
views) should operate on normalized Unix seconds.
"""

from typing import Any


def _trunc_div(value: int, divisor: int) -> int:
    """Integer division truncated toward zero (for completeness on negatives)."""
    if value >= 0:
        return value // divisor
    return -((-value) // divisor)


def _coerce_int(value: Any) -> int:
    """
    Best-effort integer coercion for timestamp-like values.

    Supports:
    - int/float
    - decimal strings and 0x-prefixed hex strings
    - bytes/bytearray as unsigned big-endian integers
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, (bytes, bytearray)):
        if not value:
            raise ValueError("empty bytes")
        return int.from_bytes(bytes(value), "big", signed=False)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError("empty string")
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s)
    return int(value)


def normalize_unix_timestamp_seconds(value: Any) -> int:
    """
    Normalize epoch timestamps to Unix seconds.

    Heuristics by magnitude:
    - abs(value) >= 1e18: nanoseconds
    - abs(value) >= 1e15: microseconds
    - abs(value) >= 1e12: milliseconds
    - otherwise: seconds
    """
    raw = _coerce_int(value)
    magnitude = abs(raw)
    if magnitude >= 10**18:
        return _trunc_div(raw, 10**9)
    if magnitude >= 10**15:
        return _trunc_div(raw, 10**6)
    if magnitude >= 10**12:
        return _trunc_div(raw, 10**3)
    return raw


def maybe_normalize_unix_timestamp_seconds(value: Any) -> int | None:
    """Return normalized Unix seconds or None if coercion fails."""
    if value is None:
        return None
    try:
        return normalize_unix_timestamp_seconds(value)
    except Exception:
        return None

