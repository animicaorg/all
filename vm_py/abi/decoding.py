"""
Inverse decoder for Animica's Python-VM ABI (see encoding.py).

Conventions mirrored from encoder:
- bool:               1 byte (0x00/0x01)
- uintN:              LEB128(len) || big-endian magnitude (minimal; 0 -> 0x00)
- intN (signed):      LEB128(len) || (sign || magnitude)
                      sign = 0x00 (>=0) or 0x01 (<0)
- bytes (dynamic):    LEB128(len) || raw bytes
- bytesN (fixed):     raw bytes (exactly N)
- address:            LEB128(len) || UTF-8 (bech32m string, e.g. "anim1…")

Top-level:
- decode_value(buf, typ, offset=0, strict=True) -> (value, new_offset)
- decode_args(buf, types, offset=0, strict=True) -> (list, new_offset)

`strict=True` enforces minimal encodings and width bounds.
"""

from __future__ import annotations

from typing import Any, List, Sequence, Tuple, Union

from .types import coerce_address  # for HRP validation & bech32m sanity
from .types import (ABITypeError, AddressType, BoolType, BytesType, IntType,
                    UIntType, ValidationError, parse_type)

__all__ = [
    "uvarint_decode",
    "decode_bool",
    "decode_uint",
    "decode_int",
    "decode_bytes",
    "decode_address",
    "decode_value",
    "decode_args",
]


# ──────────────────────────────────────────────────────────────────────────────
# Varint (unsigned LEB128)
# ──────────────────────────────────────────────────────────────────────────────


def uvarint_decode(buf: bytes, offset: int = 0) -> Tuple[int, int]:
    """
    Decode unsigned LEB128 at buf[offset:].
    Returns (value, new_offset).
    Raises ValueError on malformed input.
    """
    n = 0
    shift = 0
    i = offset
    # Allow arbitrarily large integers; bound iterations by buffer length
    while i < len(buf):
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return n, i
        shift += 7
        if shift > 8 * 8 * 1024:  # absurdly high guard
            raise ValueError("uvarint too large or malformed")
    raise ValueError("truncated uvarint")


# ──────────────────────────────────────────────────────────────────────────────
# Primitive decoders
# ──────────────────────────────────────────────────────────────────────────────


def _read_exact(buf: bytes, offset: int, n: int) -> Tuple[bytes, int]:
    j = offset + n
    if j > len(buf):
        raise ValueError("truncated payload")
    return buf[offset:j], j


def _enforce_minimal_unsigned(mag: bytes) -> None:
    # minimal: 0 => b"\x00"; otherwise no leading zero
    if len(mag) == 0:
        raise ValueError("empty magnitude")
    if len(mag) > 1 and mag[0] == 0x00:
        raise ValueError("non-minimal unsigned magnitude")


def decode_bool(
    buf: bytes, offset: int = 0, *, strict: bool = True
) -> Tuple[bool, int]:
    b, j = _read_exact(buf, offset, 1)
    if b[0] == 0x00:
        return False, j
    if b[0] == 0x01:
        return True, j
    if strict:
        raise ValueError("invalid boolean value")
    # non-strict: treat any non-zero as True
    return True, j


def decode_uint(
    buf: bytes, offset: int = 0, *, bits: int = 256, strict: bool = True
) -> Tuple[int, int]:
    length, i = uvarint_decode(buf, offset)
    mag, j = _read_exact(buf, i, length)
    if strict:
        _enforce_minimal_unsigned(mag)
    # big-endian magnitude
    v = int.from_bytes(mag, "big", signed=False)
    if v < 0:
        raise ValueError("decoded negative for uint")
    if strict and v.bit_length() > bits:
        raise ValueError(f"uint{bits} overflow")
    return v, j


def decode_int(
    buf: bytes, offset: int = 0, *, bits: int = 256, strict: bool = True
) -> Tuple[int, int]:
    length, i = uvarint_decode(buf, offset)
    payload, j = _read_exact(buf, i, length)
    if len(payload) < 1:
        raise ValueError("signed int payload too short")
    sign = payload[0]
    if sign not in (0x00, 0x01):
        raise ValueError("invalid sign byte for signed int")
    mag = payload[1:] or b"\x00"
    if strict:
        _enforce_minimal_unsigned(mag)
    v = int.from_bytes(mag, "big", signed=False)
    if sign == 0x01:
        v = -v
    # width check in two's magnitude sense: ensure |v| fits in bits
    if strict and (abs(v).bit_length() > bits):
        raise ValueError(f"int{bits} overflow")
    return v, j


def decode_bytes(
    buf: bytes,
    offset: int = 0,
    *,
    fixed_len: int | None = None,
    max_len: int | None = None,
    strict: bool = True,
) -> Tuple[bytes, int]:
    if fixed_len is not None:
        out, j = _read_exact(buf, offset, fixed_len)
        return out, j
    length, i = uvarint_decode(buf, offset)
    if max_len is not None and strict and length > max_len:
        raise ValueError("bytes length exceeds max_len")
    out, j = _read_exact(buf, i, length)
    return out, j


def decode_address(
    buf: bytes,
    offset: int = 0,
    *,
    hrp: str = "anim",
    strict: bool = True,
) -> Tuple[str, int]:
    length, i = uvarint_decode(buf, offset)
    b, j = _read_exact(buf, i, length)
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError("address is not valid UTF-8") from e
    # Validate bech32m & HRP using shared helpers (raises ValidationError on fail)
    try:
        normalized = coerce_address(s, hrp=hrp)
    except ValidationError as e:
        if strict:
            raise
        normalized = s  # non-strict: return as-is
    return normalized, j


# ──────────────────────────────────────────────────────────────────────────────
# High-level dispatch
# ──────────────────────────────────────────────────────────────────────────────


def decode_value(
    buf: bytes,
    typ: Union[str, IntType, UIntType, BytesType, BoolType, AddressType],
    offset: int = 0,
    *,
    strict: bool = True,
) -> Tuple[Any, int]:
    """
    Decode a single value of the given ABI type from buf[offset:].
    Returns (value, new_offset).
    """
    if isinstance(typ, str):
        typ = parse_type(typ)

    if isinstance(typ, BoolType):
        return decode_bool(buf, offset, strict=strict)

    if isinstance(typ, UIntType):
        return decode_uint(buf, offset, bits=typ.bits, strict=strict)

    if isinstance(typ, IntType):
        return decode_int(buf, offset, bits=typ.bits, strict=strict)

    if isinstance(typ, BytesType):
        return decode_bytes(
            buf,
            offset,
            fixed_len=typ.fixed_len,
            max_len=(typ.max_len if typ.fixed_len is None else None),
            strict=strict,
        )

    if isinstance(typ, AddressType):
        return decode_address(buf, offset, hrp=typ.hrp, strict=strict)

    raise ABITypeError(f"unsupported ABI type: {typ!r}")


def decode_args(
    buf: bytes,
    types: Sequence[Union[str, IntType, UIntType, BytesType, BoolType, AddressType]],
    offset: int = 0,
    *,
    strict: bool = True,
) -> Tuple[List[Any], int]:
    """
    Decode a sequence of arguments encoded as:
        LEB128(count) || item1 || item2 || ... || itemN
    where each item matches `types[i]`.
    Returns (list_of_values, new_offset).
    """
    count, i = uvarint_decode(buf, offset)
    if strict and count != len(types):
        raise ABITypeError(
            f"argument count mismatch: encoded={count} expected={len(types)}"
        )
    out: List[Any] = []
    # If encoded count is larger than provided types (non-strict), only decode as many as types
    steps = min(count, len(types))
    for idx in range(steps):
        v, i = decode_value(buf, types[idx], i, strict=strict)
        out.append(v)
    # If strict and encoded count > provided types, that's an error
    if strict and count != steps:
        raise ABITypeError("encoded argument count exceeds provided type list")
    return out, i
