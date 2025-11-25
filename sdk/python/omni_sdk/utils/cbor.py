"""
Deterministic (canonical) CBOR encoder/decoder.

Goals
-----
- Produce *deterministic* byte-for-byte CBOR for the subset of types we use in
  transactions, receipts, ABI payloads, etc.
- No heavy runtime dependency. If the optional `cbor2` package is installed,
  we’ll use it (with canonical mode) for decoding and can mirror its behavior
  for encoding; otherwise we fall back to a small, self-contained encoder/
  decoder that covers our needed subset.

Supported types
---------------
- None, bool
- int (± 64-bit range, minimally encoded)
- bytes, bytearray, memoryview
- str (UTF-8)
- list/tuple (definite length)
- dict with keys in {str, bytes, int}; keys are sorted by their **CBOR-encoded
  bytes** (RFC 8949 deterministic ordering).

Floats are encoded as IEEE 754 binary64 (major type 7, addl 27) for stability.
This is deterministic but not "shortest" in the RFC sense; that trade-off keeps
the implementation small and avoids half/float surprises. If you need shortest-
form canonical floats, prefer installing `cbor2` and using that path.

API
---
- dumps(obj) -> bytes
- loads(data: bytes|bytearray|memoryview) -> object
- dump_hex(obj, prefix=True) -> str
- CBOREncodeError / CBORDecodeError
"""

from __future__ import annotations

import struct
from typing import Any, Iterable, Mapping, Tuple, Union, Optional

from .bytes import ensure_bytes, to_hex

try:  # Optional fast/complete path
    import cbor2 as _cbor2  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    _cbor2 = None  # type: ignore


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------

class CBOREncodeError(ValueError):
    pass


class CBORDecodeError(ValueError):
    pass


BytesLike = Union[bytes, bytearray, memoryview]

# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def dumps(obj: Any) -> bytes:
    """Encode *obj* to deterministic CBOR bytes."""
    if _cbor2 is not None:
        # canonical=True ensures deterministic map-key ordering/minimal ints
        # timezone/float handling is left to defaults (binary64).
        try:
            return _cbor2.dumps(obj, canonical=True)
        except Exception as e:  # fall back to our encoder on corner-cases
            raise CBOREncodeError(str(e)) from e
    return _encode(obj)


def loads(data: BytesLike) -> Any:
    """Decode CBOR *data* (bytes-like) into Python objects."""
    buf = ensure_bytes(data)
    if _cbor2 is not None:
        try:
            return _cbor2.loads(buf)
        except Exception as e:
            raise CBORDecodeError(str(e)) from e
    return _decode(buf)


def dump_hex(obj: Any, *, prefix: bool = True) -> str:
    """Convenience: encode and return hex string."""
    return to_hex(dumps(obj), prefix=prefix)


# -----------------------------------------------------------------------------
# Minimal deterministic encoder (fallback)
# -----------------------------------------------------------------------------

_MT_UINT = 0
_MT_NINT = 1
_MT_BYTES = 2
_MT_TEXT = 3
_MT_ARRAY = 4
_MT_MAP = 5
_MT_TAG = 6
_MT_SIMPLE = 7


def _hdr(mt: int, ai: int) -> bytes:
    return bytes([(mt << 5) | ai])


def _enc_uint(mt: int, val: int) -> bytes:
    if not (0 <= val):
        raise CBOREncodeError("uint encoder got negative")
    if val < 24:
        return _hdr(mt, val)
    if val <= 0xFF:
        return _hdr(mt, 24) + struct.pack(">B", val)
    if val <= 0xFFFF:
        return _hdr(mt, 25) + struct.pack(">H", val)
    if val <= 0xFFFFFFFF:
        return _hdr(mt, 26) + struct.pack(">I", val)
    if val <= 0xFFFFFFFFFFFFFFFF:
        return _hdr(mt, 27) + struct.pack(">Q", val)
    raise CBOREncodeError("integer too large for canonical encoder")


def _enc_int(i: int) -> bytes:
    if i >= 0:
        return _enc_uint(_MT_UINT, i)
    # Negative integers are encoded as (-1 - n) with major type 1
    n = -1 - i
    return _enc_uint(_MT_NINT, n)


def _enc_bytes(b: BytesLike) -> bytes:
    b = ensure_bytes(b)
    return _enc_uint(_MT_BYTES, len(b)) + b


def _enc_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return _enc_uint(_MT_TEXT, len(b)) + b


def _enc_array(seq: Iterable[Any]) -> bytes:
    items = list(seq)
    out = [_enc_uint(_MT_ARRAY, len(items))]
    for x in items:
        out.append(_encode(x))
    return b"".join(out)


def _enc_map(m: Mapping[Any, Any]) -> bytes:
    # Deterministic ordering: sort by the CBOR-encoded byte representation of keys.
    entries: list[Tuple[bytes, Any, Any]] = []
    for k, v in m.items():
        kb = _encode_key(k)
        entries.append((kb, k, v))
    entries.sort(key=lambda t: t[0])
    out_parts = [_enc_uint(_MT_MAP, len(entries))]
    for kb, _k, v in entries:
        out_parts.append(kb)
        out_parts.append(_encode(v))
    return b"".join(out_parts)


def _enc_float64(f: float) -> bytes:
    # Encode as binary64 deterministically.
    return _hdr(_MT_SIMPLE, 27) + struct.pack(">d", f)


def _encode(obj: Any) -> bytes:
    if obj is None:
        return b"\xf6"  # simple null
    if obj is True:
        return b"\xf5"  # true
    if obj is False:
        return b"\xf4"  # false
    if isinstance(obj, int):
        return _enc_int(obj)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return _enc_bytes(obj)
    if isinstance(obj, str):
        return _enc_str(obj)
    if isinstance(obj, (list, tuple)):
        return _enc_array(obj)
    if isinstance(obj, dict):
        return _enc_map(obj)
    if isinstance(obj, float):
        return _enc_float64(obj)

    raise CBOREncodeError(f"unsupported type for CBOR: {type(obj)!r}")


def _encode_key(key: Any) -> bytes:
    """Encode a map key under the same rules; restrict to str/bytes/int for safety."""
    if isinstance(key, (str, bytes, bytearray, memoryview, int)):
        return _encode(key)
    raise CBOREncodeError(f"unsupported map key type: {type(key)!r}")


# -----------------------------------------------------------------------------
# Minimal decoder (fallback)
# -----------------------------------------------------------------------------

class _Reader:
    __slots__ = ("b", "i", "n")

    def __init__(self, b: bytes):
        self.b = b
        self.i = 0
        self.n = len(b)

    def read(self, n: int) -> bytes:
        if self.i + n > self.n:
            raise CBORDecodeError("truncated input")
        s = self.b[self.i : self.i + n]
        self.i += n
        return s

    def read_u(self, n: int) -> int:
        if n == 1:
            return struct.unpack(">B", self.read(1))[0]
        if n == 2:
            return struct.unpack(">H", self.read(2))[0]
        if n == 4:
            return struct.unpack(">I", self.read(4))[0]
        if n == 8:
            return struct.unpack(">Q", self.read(8))[0]
        raise CBORDecodeError("bad uint length")


def _read_ai(r: _Reader, ai: int) -> int:
    if ai < 24:
        return ai
    if ai == 24:
        return r.read_u(1)
    if ai == 25:
        return r.read_u(2)
    if ai == 26:
        return r.read_u(4)
    if ai == 27:
        return r.read_u(8)
    raise CBORDecodeError("indefinite lengths not supported")


def _decode_one(r: _Reader) -> Any:
    ib = r.read(1)[0]
    mt = ib >> 5
    ai = ib & 0x1F

    if mt in (_MT_UINT, _MT_NINT):
        val = _read_ai(r, ai)
        if mt == _MT_UINT:
            return val
        # negative
        return -1 - val

    if mt == _MT_BYTES:
        ln = _read_ai(r, ai)
        return r.read(ln)

    if mt == _MT_TEXT:
        ln = _read_ai(r, ai)
        try:
            return r.read(ln).decode("utf-8")
        except Exception as e:
            raise CBORDecodeError("invalid UTF-8 string") from e

    if mt == _MT_ARRAY:
        ln = _read_ai(r, ai)
        return [_decode_one(r) for _ in range(ln)]

    if mt == _MT_MAP:
        ln = _read_ai(r, ai)
        out = {}
        last_key_bytes: Optional[bytes] = None
        for _ in range(ln):
            # For deterministic maps, we could enforce key order by comparing
            # encoded bytes, but we keep decoding simple and just build dict.
            k_start = r.i
            k = _decode_one(r)
            k_bytes = r.b[k_start:r.i]
            if last_key_bytes is not None and last_key_bytes > k_bytes:
                # Not fatal for general CBOR, but we enforce to catch non-deterministic encodings early.
                raise CBORDecodeError("map keys out of deterministic order")
            last_key_bytes = k_bytes
            v = _decode_one(r)
            out[k] = v
        return out

    if mt == _MT_TAG:
        # Consume tag and return the tagged value transparently.
        _ = _read_ai(r, ai)
        return _decode_one(r)

    if mt == _MT_SIMPLE:
        if ai < 20:
            raise CBORDecodeError("unsupported simple value")
        if ai == 20:
            return False
        if ai == 21:
            return True
        if ai == 22:
            return None
        if ai == 27:
            # float64
            return struct.unpack(">d", r.read(8))[0]
        raise CBORDecodeError("unsupported simple/float encoding")

    raise CBORDecodeError("unknown major type")


def _decode(b: bytes) -> Any:
    r = _Reader(b)
    obj = _decode_one(r)
    if r.i != r.n:
        raise CBORDecodeError("extra trailing bytes")
    return obj


__all__ = [
    "dumps",
    "loads",
    "dump_hex",
    "CBOREncodeError",
    "CBORDecodeError",
]
