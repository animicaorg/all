"""
core.utils.serialization
========================

Deterministic JSON helpers for specs, fixtures, and RPC views.

Goals
-----
* **Canonical bytes**: objects → compact JSON (UTF-8, no spaces), with keys sorted
  by Unicode code point and stable container normalization.
* **Big integers**: optionally encode integers outside JS-safe range (|n| >= 2^53)
  as decimal strings or 0x-hex strings (configurable).
* **Bytes**: bytes/bytearray are encoded as 0x-prefixed lowercase hex strings.
* **No floats by default**: to avoid platform quirks, we reject float values unless
  explicitly allowed.

APIs
----
- canonical_dumps(obj, *, bigint="string"|"hex"|"raw", allow_floats=False) -> bytes
- canonical_dumpstr(obj, **kw) -> str
- canonical_hash_equal(a, b, **kw) -> bool     # compare canonical encodings
- encode_bigints(obj, mode="string"|"hex"|"raw") -> obj (deep copy)
- forbid_floats(obj) -> None (raises on first float)
- normalize_containers(obj) -> obj (set→sorted list, tuple→list)
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple, Union

import json
from decimal import Decimal

try:
    import orjson as _orjson  # type: ignore
except Exception:  # pragma: no cover - optional dep
    _orjson = None  # type: ignore

from .bytes import BytesLike


_JS_SAFE_MAX = (1 << 53) - 1  # Number.MAX_SAFE_INTEGER
_JS_SAFE_MIN = -_JS_SAFE_MAX


# -------------------------------
# Container & type normalization
# -------------------------------

def _is_js_unsafe_int(n: int) -> bool:
    return not (_JS_SAFE_MIN <= n <= _JS_SAFE_MAX)


def normalize_containers(obj: Any) -> Any:
    """
    Convert non-JSON containers to JSON-safe forms deterministically:
      * set → sorted list
      * tuple → list
      * mapping types kept as dict but keys must be str (enforced by dumps)
    Recurses into nested structures.
    """
    if isinstance(obj, dict):
        return {k: normalize_containers(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize_containers(v) for v in obj]
    if isinstance(obj, set):
        return [normalize_containers(v) for v in sorted(obj, key=_canon_sort_key)]
    return obj


def _canon_sort_key(x: Any) -> Any:
    """
    Sorting helper for sets/tuples when we must produce a stable order across types.
    Rule of thumb: compare by type name then by value (stringified for bytes).
    """
    if isinstance(x, (bytes, bytearray)):
        return ("bytes", x.hex())
    if isinstance(x, str):
        return ("str", x)
    if isinstance(x, int):
        return ("int", x)
    if isinstance(x, bool):
        return ("bool", x)
    if x is None:
        return ("none", 0)
    # Fallback to repr to avoid TypeError in heterogeneous sets.
    return (type(x).__name__, repr(x))


def forbid_floats(obj: Any, *, path: str = "$") -> None:
    """
    Raise ValueError if a float is found anywhere in `obj`.
    This enforces deterministic encodings (floats are lossy/ambiguous in JSON).
    """
    if isinstance(obj, float):
        raise ValueError(f"float not allowed at {path} (use Decimal or string)")
    if isinstance(obj, dict):
        for k, v in obj.items():
            forbid_floats(v, path=f"{path}.{k}")
    elif isinstance(obj, (list, tuple, set)):
        for i, v in enumerate(obj):
            forbid_floats(v, path=f"{path}[{i}]")


# -------------------------------
# BigInt handling
# -------------------------------

def encode_bigints(obj: Any, *, mode: str = "string") -> Any:
    """
    Recursively encode integers per mode:

      - "string": integers outside JS-safe range become decimal strings.
      - "hex":    integers outside JS-safe range become 0x-prefixed lowercase hex.
      - "raw":    leave as Python int (JSON will still encode as numbers).

    Small integers (|n| <= 2^53−1) are left as numbers unless mode == "hex_force"
    (not exposed) to keep typical JSON ergonomic.

    Bytes are untouched here (handled by default serializer).
    """
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        if mode == "raw" or not _is_js_unsafe_int(obj):
            return obj
        if mode == "string":
            return str(obj)
        if mode == "hex":
            sign = "-" if obj < 0 else ""
            mag = abs(obj)
            return f"{sign}0x{mag:x}"
        raise ValueError(f"unknown bigint mode: {mode}")
    if isinstance(obj, (str, bytes, bytearray)):
        return obj
    if isinstance(obj, dict):
        return {k: encode_bigints(v, mode=mode) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [encode_bigints(v, mode=mode) for v in obj]
    if isinstance(obj, set):
        return [encode_bigints(v, mode=mode) for v in sorted(obj, key=_canon_sort_key)]
    return obj


# -------------------------------
# JSON encode helpers
# -------------------------------

def _default_json(obj: Any) -> Any:
    """
    Default serializer for unsupported types:
      * bytes/bytearray → 0x lowercase hex string
      * Decimal → decimal string (no exponent)
    """
    if isinstance(obj, (bytes, bytearray)):
        return "0x" + bytes(obj).hex()
    if isinstance(obj, Decimal):
        # Produce a plain string with no exponent to avoid locale/scientific notation issues
        return format(obj, "f").rstrip("0").rstrip(".") if "." in format(obj, "f") else format(obj, "f")
    # dataclasses etc. could be supported here if needed
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def canonical_dumps(
    obj: Any,
    *,
    bigint: str = "string",
    allow_floats: bool = False,
    ensure_ascii: bool = False,
) -> bytes:
    """
    Encode `obj` to **canonical** JSON bytes:
      * keys sorted
      * no insignificant whitespace (separators=(',', ':'))
      * UTF-8
      * sets normalized
      * bigints encoded per `bigint` policy: "string" (default), "hex", or "raw"
      * bytes → "0x..." strings
      * floats rejected unless allow_floats=True

    Returns UTF-8 bytes.
    """
    if not allow_floats:
        forbid_floats(obj)

    norm = normalize_containers(obj)
    norm = encode_bigints(norm, mode=bigint)

    if _orjson:
        # orjson returns bytes already; OPT_SORT_KEYS ensures canonical order.
        # We still rely on our _default_json for bytes/Decimal by pre-walking;
        # orjson will error on unsupported types, so we pre-process only those.
        def _orjson_default(o: Any):
            return _default_json(o)

        return _orjson.dumps(
            norm,
            option=(
                _orjson.OPT_SORT_KEYS
                | _orjson.OPT_OMIT_MICROSECONDS
                | (_orjson.OPT_ESCAPE_FORWARD_SLASHES)
                | (_orjson.OPT_NON_STR_KEYS)  # we still expect str keys, but this is harmless
            ),
            default=_orjson_default,
        )

    # Fallback to stdlib json
    return json.dumps(
        norm,
        default=_default_json,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=ensure_ascii,
        allow_nan=False,
    ).encode("utf-8")


def canonical_dumpstr(obj: Any, **kw: Any) -> str:
    """Like canonical_dumps but returns a str."""
    return canonical_dumps(obj, **kw).decode("utf-8")


def canonical_hash_equal(a: Any, b: Any, **kw: Any) -> bool:
    """
    Compare canonical encodings for equality without allocating Python objects.
    Handy for tests where order or bigint policy must be identical.
    """
    return canonical_dumps(a, **kw) == canonical_dumps(b, **kw)


# -------------------------------
# JSON decode helpers
# -------------------------------

def loads_allow_hex_bigint(s: Union[str, bytes, bytearray]) -> Any:
    """
    Load JSON but post-process string values that *look* like 0x-hex integers
    into Python ints. This is a convenience for tools/tests; not used in consensus.
    """
    if isinstance(s, (bytes, bytearray)):
        s = s.decode("utf-8")
    obj = json.loads(s)

    def _convert(x: Any) -> Any:
        if isinstance(x, str) and x.startswith(("0x", "-0x")):
            try:
                return int(x, 16)
            except ValueError:
                return x
        if isinstance(x, list):
            return [_convert(v) for v in x]
        if isinstance(x, dict):
            return {k: _convert(v) for k, v in x.items()}
        return x

    return _convert(obj)


# -------------------------------
# Pretty helpers (non-canonical)
# -------------------------------

def pretty_dumps(obj: Any) -> str:
    """
    Human-friendly pretty JSON (2-space indent, sorted keys). Not canonical.
    """
    return json.dumps(
        normalize_containers(obj),
        default=_default_json,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    )


__all__ = [
    "canonical_dumps",
    "canonical_dumpstr",
    "canonical_hash_equal",
    "encode_bigints",
    "forbid_floats",
    "normalize_containers",
    "loads_allow_hex_bigint",
    "pretty_dumps",
]
