"""
capabilities.cbor.codec
=======================

Canonical CBOR encode/decode helpers used by capabilities schemas:

- Job Request / Receipt / Result Record envelopes (CBOR)
- Deterministic, canonical map ordering (RFC 7049/8949 "canonical CBOR")
- Shortest integer encodings; stable bytes/strings handling

Backends
--------
Prefers `msgspec` (fast, zero-copy) if available; falls back to `cbor2`.
Both are configured for canonical encoding. Behavior is aligned so that
round-trips are stable across backends.

Public API
----------
dumps(obj) -> bytes
loads(data: (bytes|bytearray|memoryview)) -> Any

Notes
-----
* Keys in mappings MUST be of type (str | int | bytes). Floats or other
  non-canonical keys are rejected to avoid non-determinism across
  implementations.
* Dataclasses and Enums are converted to plain Python types before encoding.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Tuple, Union

_BACKEND = "cbor2"
_HAS_MSGSPEC = False

# --- Optional fast backend: msgspec -------------------------------------------------
try:
    import msgspec  # type: ignore
    from msgspec import cbor as _cbor  # type: ignore

    _HAS_MSGSPEC = True
    _BACKEND = "msgspec"
except Exception:  # pragma: no cover - if msgspec isn't installed
    _HAS_MSGSPEC = False

# --- Fallback backend: cbor2 -------------------------------------------------------
if not _HAS_MSGSPEC:
    try:
        import cbor2  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Neither msgspec nor cbor2 is available. Install one of them:\n"
            "  pip install msgspec\n"
            "  # or\n"
            "  pip install cbor2"
        ) from e


class CBORError(Exception):
    """Raised for canonical CBOR violations or encode/decode failures."""


_KeyType = Union[str, int, bytes]


def _is_key_type(k: Any) -> bool:
    return isinstance(k, (str, int, bytes))


def _to_plain(obj: Any) -> Any:
    """Convert dataclasses/Enums/bytearray/memoryview etc. to plain types."""
    if obj is None or isinstance(obj, (bool, int, float, str, bytes)):
        return obj
    if isinstance(obj, (bytearray, memoryview)):
        return bytes(obj)
    if is_dataclass(obj):
        return {k: _to_plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Enum):
        # Prefer the value; ensures cross-lang stability
        return _to_plain(obj.value)
    if isinstance(obj, Mapping):
        # Enforce canonical key types and recursively plain-ify
        out: Dict[_KeyType, Any] = {}
        for k, v in obj.items():
            if not _is_key_type(k):
                raise CBORError(
                    f"Non-canonical mapping key type {type(k).__name__}; "
                    "only str|int|bytes are allowed"
                )
            out[k] = _to_plain(v)
        return out
    if isinstance(obj, (tuple, list)):
        return [ _to_plain(x) for x in obj ]
    # Fallback: try __dict__ then repr()
    if hasattr(obj, "__dict__"):
        return _to_plain(vars(obj))
    return repr(obj)


def _validate_mapping_keys(obj: Any) -> None:
    """Walk the structure and ensure all mapping keys are canonical types."""
    stack: list[Any] = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Mapping):
            for k, v in cur.items():
                if not _is_key_type(k):
                    raise CBORError(
                        f"Non-canonical mapping key type {type(k).__name__}; "
                        "only str|int|bytes are allowed"
                    )
                stack.append(v)
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)


# -------------------------- Encode / Decode (public) --------------------------------

def dumps(obj: Any) -> bytes:
    """
    Encode to canonical CBOR bytes.

    Ensures:
    - Deterministic map ordering (canonical)
    - Shortest integer representation
    - Rejects non-canonical mapping keys (float, custom objects, etc.)
    """
    plain = _to_plain(obj)
    _validate_mapping_keys(plain)

    if _HAS_MSGSPEC:
        # msgspec's canonical CBOR encoder
        # Newer msgspec versions accept canonical=True at Encoder construction.
        # Fallback to encode() path with canonical=True if construction fails.
        try:
            enc = _cbor.Encoder(canonical=True)  # type: ignore[arg-type]
            return enc.encode(plain)
        except TypeError:
            # Older API: use function-level flag if supported
            try:
                return _cbor.encode(plain, canonical=True)  # type: ignore[call-arg]
            except TypeError as e:
                # As a last resort, use default encoder (may not be fully canonical)
                raise CBORError(
                    "msgspec installed but doesn't support canonical CBOR. "
                    "Please upgrade msgspec or install cbor2."
                ) from e
    else:
        # cbor2 supports canonical mode directly
        try:
            return cbor2.dumps(plain, canonical=True)  # type: ignore[name-defined]
        except Exception as e:  # pragma: no cover
            raise CBORError(f"cbor2 canonical encode failed: {e}") from e


def loads(data: Union[bytes, bytearray, memoryview]) -> Any:
    """
    Decode CBOR bytes. This does not re-order maps (that's an encode-time property)
    but returns standard Python types. Bytes are preserved as `bytes`.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise CBORError("loads() expects bytes-like input")
    try:
        if _HAS_MSGSPEC:
            return _cbor.decode(data)
        else:
            return cbor2.loads(data)  # type: ignore[name-defined]
    except Exception as e:
        raise CBORError(f"CBOR decode failed: {e}") from e


# Friendly aliases
encode = dumps
decode = loads

__all__ = ["dumps", "loads", "encode", "decode", "CBORError", "_BACKEND"]


# ------------------------------- Self-test (optional) --------------------------------
if __name__ == "__main__":  # pragma: no cover
    sample = {
        "n": 7,
        "b": b"\x00\x01",
        "m": {"a": 1, "z": 2, "k": [1, 2, 3]},
        "i": 2**63,  # big int
        "list": [{"x": 1}, {"y": 2}],
    }
    encoded = dumps(sample)
    decoded = loads(encoded)
    assert decoded == sample, (decoded, sample)
    print(f"[codec] backend={_BACKEND} bytes={len(encoded)} ok")
