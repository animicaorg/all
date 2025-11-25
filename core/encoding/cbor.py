from __future__ import annotations

"""
Canonical CBOR codec (deterministic)
------------------------------------

A small, dependency-light encoder/decoder that follows RFC 8949 "Deterministic
Encoding" for the subset of CBOR types we use in consensus objects:

Supported Python types:
- None, bool
- int (arbitrary precision; uses tags 2/3 for bignums beyond 64-bit)
- bytes
- str (UTF-8, must be valid)
- list/tuple
- dict (keys must be int/bytes/str; encoded with *deterministic* ordering)
- dataclasses (treated as maps of field name -> value)

Not supported (by design for consensus):
- float/Decimal
- indefinite-length items
- half/float16/float32/float64 numbers
- simple values other than false/true/null

Deterministic map ordering:
Sort keys by the bytewise lexicographic order of the *deterministic CBOR
encodings of the keys* (RFC 8949 §4.2.1). We pre-encode keys and sort on those
bytes; duplicates after encoding are rejected.

This module purposefully avoids optional dependencies. If you prefer a full
implementation, you can plug cbor2/msgspec in behind the same API as long as
you configure deterministic/canonical mode and enforce the same constraints.

Public API:
- dumps(obj) -> bytes
- loads(b: bytes) -> object

These are re-exported from core.encoding.__init__.
"""

from dataclasses import is_dataclass, asdict
from typing import Any, Tuple, List, Dict, Iterable

# ------------------------
# Low-level encode helpers
# ------------------------

def _ai_bytes(major: int, n: int) -> bytes:
    """Encode initial byte + additional-info for a non-negative integer length/value."""
    assert 0 <= major <= 7
    if n < 24:
        return bytes([(major << 5) | n])
    elif n <= 0xFF:
        return bytes([(major << 5) | 24, n])
    elif n <= 0xFFFF:
        return bytes([(major << 5) | 25]) + n.to_bytes(2, "big")
    elif n <= 0xFFFFFFFF:
        return bytes([(major << 5) | 26]) + n.to_bytes(4, "big")
    elif n <= 0xFFFFFFFFFFFFFFFF:
        return bytes([(major << 5) | 27]) + n.to_bytes(8, "big")
    else:
        # Caller must use bignum tags 2/3; we never put >64-bit here.
        raise OverflowError("Length/value too large for additional-info field")

def _encode_uint(n: int) -> bytes:
    assert n >= 0
    return _ai_bytes(0, n)

def _encode_nint(n: int) -> bytes:
    # Negative integer x is encoded as major type 1 with argument -(x+1)
    assert n < 0
    return _ai_bytes(1, -1 - n)

def _encode_bytes(data: bytes) -> bytes:
    return _ai_bytes(2, len(data)) + data

def _encode_text(s: str) -> bytes:
    b = s.encode("utf-8", "strict")
    return _ai_bytes(3, len(b)) + b

def _encode_array(items: Iterable[bytes], count: int) -> bytes:
    out = bytearray(_ai_bytes(4, count))
    for it in items:
        out += it
    return bytes(out)

def _encode_map(pairs: Iterable[Tuple[bytes, bytes]], count: int) -> bytes:
    out = bytearray(_ai_bytes(5, count))
    for k, v in pairs:
        out += k
        out += v
    return bytes(out)

def _encode_tag(tag: int, payload: bytes) -> bytes:
    # major type 6 = tag
    return _ai_bytes(6, tag) + payload

# ------------------------
# Canonical encoder
# ------------------------

class EncodeError(TypeError):
    pass

def _is_small_int(n: int) -> bool:
    return 0 <= n <= 0xFFFFFFFFFFFFFFFF

def _to_bignum_bytes(n: int) -> bytes:
    """Magnitude to minimal big-endian bytes without leading zeros."""
    assert n >= 0
    if n == 0:
        return b"\x00"
    length = (n.bit_length() + 7) // 8
    b = n.to_bytes(length, "big")
    # Trim leading zeros (shouldn't be any by construction)
    i = 0
    while i < len(b) - 1 and b[i] == 0:
        i += 1
    return b[i:]

def _canonical_key_order(key_bytes: bytes) -> bytes:
    """Identity — sorting by the encoded key bytes lexicographically."""
    return key_bytes

def _encode_obj(obj: Any) -> bytes:
    # dataclasses become dicts
    if is_dataclass(obj):
        obj = asdict(obj)

    if obj is None:
        return bytes([0xF6])  # null
    if obj is False:
        return bytes([0xF4])
    if obj is True:
        return bytes([0xF5])

    if isinstance(obj, int):
        if obj >= 0:
            if _is_small_int(obj):
                return _encode_uint(obj)
            # Positive bignum: tag(2) + bstr(magnitude)
            return _encode_tag(2, _encode_bytes(_to_bignum_bytes(obj)))
        else:
            m = -1 - obj
            if _is_small_int(m):
                return _encode_nint(obj)
            # Negative bignum: tag(3) + bstr(magnitude of -1 - n)
            return _encode_tag(3, _encode_bytes(_to_bignum_bytes(m)))

    if isinstance(obj, bytes):
        return _encode_bytes(obj)

    if isinstance(obj, str):
        # Ensure valid UTF-8; strict encoder will raise otherwise
        return _encode_text(obj)

    if isinstance(obj, (list, tuple)):
        enc_items = [_encode_obj(x) for x in obj]
        return _encode_array(enc_items, len(enc_items))

    if isinstance(obj, dict):
        # Keys must be int/bytes/str (or dataclass fields -> str)
        enc_kv: List[Tuple[bytes, bytes]] = []
        for k, v in obj.items():
            if is_dataclass(k):
                raise EncodeError("dataclass not allowed as map key")
            if isinstance(k, (int, bytes, str)):
                k_enc = _encode_obj(k)
            else:
                raise EncodeError(f"unsupported map key type: {type(k).__name__}")
            v_enc = _encode_obj(v)
            enc_kv.append((k_enc, v_enc))
        # Sort by key's deterministic encoding bytes
        enc_kv.sort(key=lambda kv: _canonical_key_order(kv[0]))
        # Reject duplicate keys post-encoding (non-canonical input)
        for i in range(1, len(enc_kv)):
            if enc_kv[i - 1][0] == enc_kv[i][0]:
                raise EncodeError("duplicate map keys after canonicalization")
        return _encode_map(enc_kv, len(enc_kv))

    # Floats and others are not allowed in consensus encodings
    raise EncodeError(f"unsupported type for canonical CBOR: {type(obj).__name__}")

def dumps(obj: Any) -> bytes:
    """
    Encode `obj` to canonical CBOR bytes with deterministic map ordering.
    """
    return _encode_obj(obj)

# ------------------------
# Minimal decoder (strict)
# ------------------------

class DecodeError(ValueError):
    pass

class _Buf:
    __slots__ = ("b", "i", "n")
    def __init__(self, b: bytes):
        self.b = memoryview(b)
        self.i = 0
        self.n = len(b)
    def get(self, k: int) -> bytes:
        if self.i + k > self.n:
            raise DecodeError("truncated")
        out = self.b[self.i:self.i+k].tobytes()
        self.i += k
        return out
    def get1(self) -> int:
        if self.i >= self.n:
            raise DecodeError("truncated")
        v = self.b[self.i]
        self.i += 1
        return int(v)

def _read_ai(buf: _Buf) -> Tuple[int, int]:
    ib = buf.get1()
    major = ib >> 5
    ai = ib & 0x1F
    if ai < 24:
        return major, ai
    if ai == 24:
        return major, int.from_bytes(buf.get(1), "big")
    if ai == 25:
        return major, int.from_bytes(buf.get(2), "big")
    if ai == 26:
        return major, int.from_bytes(buf.get(4), "big")
    if ai == 27:
        return major, int.from_bytes(buf.get(8), "big")
    raise DecodeError("indefinite lengths are not allowed (deterministic only)")

def _decode(buf: _Buf) -> Any:
    major, ai = _read_ai(buf)

    if major == 0:
        # unsigned int
        return ai
    if major == 1:
        # negative int: value is -1 - ai
        return -1 - ai
    if major == 2:
        # bytes
        data = buf.get(ai)
        return data
    if major == 3:
        # text (UTF-8)
        data = buf.get(ai)
        try:
            return data.decode("utf-8", "strict")
        except UnicodeDecodeError as e:
            raise DecodeError(f"invalid UTF-8: {e}") from e
    if major == 4:
        # array
        out = []
        for _ in range(ai):
            out.append(_decode(buf))
        return out
    if major == 5:
        # map
        out: Dict[Any, Any] = {}
        last_key_enc: bytes | None = None
        # We must enforce deterministic ordering on decode as a sanity check.
        # We'll re-encode keys as we parse to ensure strictly increasing order.
        for _ in range(ai):
            # Snapshot index before decoding key to recover its canonical encoding
            key_start = buf.i
            key = _decode(buf)
            key_end = buf.i
            key_enc = buf.b[key_start:key_end].tobytes()
            if last_key_enc is not None and key_enc <= last_key_enc:
                raise DecodeError("map keys not in deterministic (strictly increasing) order")
            last_key_enc = key_enc
            val = _decode(buf)
            # Accept only int/bytes/str keys at runtime (mirrors encoder)
            if not isinstance(key, (int, bytes, str)):
                raise DecodeError(f"unsupported map key type at decode: {type(key).__name__}")
            if key in out:
                raise DecodeError("duplicate map key")
            out[key] = val
        return out
    if major == 6:
        # tag
        tag = ai
        # Only bignums (2/3) are supported in our deterministic subset
        if tag not in (2, 3):
            raise DecodeError(f"unsupported tag {tag}")
        m_major, m_len = _read_ai(buf)
        if m_major != 2:
            raise DecodeError("bignum tag payload must be a byte string")
        mag = buf.get(m_len)
        if len(mag) == 0:
            # Zero is encoded as a single 0x00 byte; empty is invalid
            raise DecodeError("invalid bignum magnitude")
        n = int.from_bytes(mag, "big")
        if tag == 2:
            return n
        else:
            # negative bignum: -1 - n
            return -1 - n
    if major == 7:
        # simple/float/null/bool
        if ai == 20:
            return False
        if ai == 21:
            return True
        if ai == 22:
            return None
        # We do not accept floats/simple values beyond the above
        raise DecodeError("floating point/simple values are not allowed in consensus CBOR")

    raise DecodeError(f"unknown major type: {major}")

def loads(b: bytes) -> Any:
    """
    Decode canonical CBOR bytes back to Python values, enforcing the same subset and
    ordering rules the encoder uses. Raises DecodeError on violations.
    """
    buf = _Buf(b)
    obj = _decode(buf)
    if buf.i != buf.n:
        raise DecodeError("trailing bytes")
    return obj
