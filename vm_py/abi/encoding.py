"""
Canonical ABI encoding for Animica's Python VM.

Design goals:
- Simple, deterministic, and easy to port (Python/TS/Rust).
- Length-prefixed for variable data; minimal big-endian integers.
- No implicit padding or alignment.

Primitives
----------
All primitives are encoded as a *tagless* byte sequence; higher-level
call sites (e.g., "function arguments") compose these by concatenation.

- bool:               1 byte: 0x00 (false) or 0x01 (true)
- uintN:              LEB128(len) || big-endian minimal magnitude (0 -> 0x00)
- intN (signed):      LEB128(len) || (sign || magnitude)
                      where sign is 0x00 for non-negative, 0x01 for negative,
                      and magnitude is big-endian minimal of |value|
- bytes (dynamic):    LEB128(len) || raw bytes
- bytesN (fixed):     raw bytes (exactly N)
- address:            LEB128(len) || UTF-8 bytes of the bech32m string (e.g., "anim1…")

Sequences (function arguments)
------------------------------
encode_args([types...], [values...]) =>
  LEB128(count) || item1 || item2 || ... || itemN
where each item = encode_value(value, type_spec).

This module only performs encoding; type *validation* and normalization
live in vm_py.abi.types. Decoding is provided separately.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Sequence, Tuple, Union, overload

from .types import (
    ABITypeError,
    ValidationError,
    IntType,
    UIntType,
    BytesType,
    BoolType,
    AddressType,
    parse_type,
    coerce_bool,
    coerce_uint,
    coerce_int,
    coerce_bytes,
    coerce_address,
)

__all__ = [
    "uvarint_encode",
    "encode_bool",
    "encode_uint",
    "encode_int",
    "encode_bytes",
    "encode_address",
    "encode_value",
    "encode_args",
]


# ──────────────────────────────────────────────────────────────────────────────
# Varint (unsigned LEB128) for length prefixes and counts
# ──────────────────────────────────────────────────────────────────────────────

def uvarint_encode(n: int) -> bytes:
    """
    Unsigned LEB128 encoding.

    - n must be >= 0
    - returns minimal-length representation
    """
    if not isinstance(n, int):
        raise TypeError("uvarint value must be int")
    if n < 0:
        raise ValueError("uvarint cannot encode negative values")
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


# ──────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ──────────────────────────────────────────────────────────────────────────────

def _minimal_be_unsigned(n: int) -> bytes:
    """Big-endian minimal bytes for a non-negative integer (0 → b'\\x00')."""
    if n < 0:
        raise ValueError("expected non-negative integer")
    if n == 0:
        return b"\x00"
    length = (n.bit_length() + 7) // 8
    return n.to_bytes(length, "big", signed=False)


# ──────────────────────────────────────────────────────────────────────────────
# Primitive encoders
# ──────────────────────────────────────────────────────────────────────────────

def encode_bool(value: Any) -> bytes:
    v = coerce_bool(value)
    return b"\x01" if v else b"\x00"


def encode_uint(value: Any, *, bits: int = 256) -> bytes:
    v = coerce_uint(value, bits=bits)
    mag = _minimal_be_unsigned(v)
    return uvarint_encode(len(mag)) + mag


def encode_int(value: Any, *, bits: int = 256) -> bytes:
    v = coerce_int(value, bits=bits, signed=True)
    sign = 0x00 if v >= 0 else 0x01
    mag = _minimal_be_unsigned(abs(v))
    payload = bytes([sign]) + mag
    return uvarint_encode(len(payload)) + payload


def encode_bytes(value: Any, *, fixed_len: int | None = None, max_len: int | None = None) -> bytes:
    """
    Encode bytes (or 0x-hex string). For fixed_len, the output is the raw bytes with
    no length prefix. For dynamic bytes, the output is LEB128(len) || bytes.
    """
    b = coerce_bytes(value, fixed_len=fixed_len, max_len=max_len)
    if fixed_len is not None:
        # Fixed-sized "bytesN" have no explicit length prefix by convention.
        return b
    return uvarint_encode(len(b)) + b


def encode_address(value: Any, *, hrp: str = "anim") -> bytes:
    addr = coerce_address(value, hrp=hrp)
    b = addr.encode("utf-8")
    return uvarint_encode(len(b)) + b


# ──────────────────────────────────────────────────────────────────────────────
# High-level dispatch
# ──────────────────────────────────────────────────────────────────────────────

@overload
def encode_value(value: Any, typ: str) -> bytes: ...
@overload
def encode_value(value: Any, typ: IntType | UIntType | BytesType | BoolType | AddressType) -> bytes: ...

def encode_value(
    value: Any,
    typ: Union[str, IntType, UIntType, BytesType, BoolType, AddressType],
) -> bytes:
    """
    Encode a single value according to the given ABI type (string or object).
    """
    if isinstance(typ, str):
        typ = parse_type(typ)

    if isinstance(typ, BoolType):
        return encode_bool(value)

    if isinstance(typ, UIntType):
        return encode_uint(value, bits=typ.bits)

    if isinstance(typ, IntType):
        return encode_int(value, bits=typ.bits)

    if isinstance(typ, BytesType):
        # For dynamic bytes: no max_len limit here (can be enforced at call sites);
        # for fixed bytesN: exact length enforced by coerce_bytes via fixed_len.
        return encode_bytes(
            value,
            fixed_len=typ.fixed_len,
            max_len=typ.max_len if typ.fixed_len is None else None,
        )

    if isinstance(typ, AddressType):
        return encode_address(value, hrp=typ.hrp)

    raise ABITypeError(f"unsupported ABI type: {typ!r}")


def encode_args(types: Sequence[Union[str, IntType, UIntType, BytesType, BoolType, AddressType]],
                values: Sequence[Any]) -> bytes:
    """
    Encode a sequence of arguments as:
        LEB128(count) || item1 || item2 || ... || itemN
    where each item = encode_value(value_i, type_i).

    Raises:
        ABITypeError or ValidationError on mismatch or invalid values.
    """
    if len(types) != len(values):
        raise ABITypeError("types and values length mismatch")
    encoded_items: List[bytes] = []
    for t, v in zip(types, values):
        encoded_items.append(encode_value(v, t))
    return uvarint_encode(len(encoded_items)) + b"".join(encoded_items)
