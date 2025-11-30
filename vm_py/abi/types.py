"""
ABI type definitions and validation for the Animica Python VM.

We keep the surface intentionally small to mirror the VM's core types:
  - int / uint (bounded width; canonical default = 256 bits)
  - bytes / bytesN (opaque byte strings, optionally fixed-length)
  - bool
  - address (bech32m string "anim1…", encoding alg_id || sha3_256(pubkey))

Utilities here *only* coerce/validate Python values; the on-wire encoding is
implemented in vm_py.abi.encoding/decoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NewType, Optional, Tuple

__all__ = [
    "ABITypeError",
    "ValidationError",
    "Address",
    "is_address",
    "normalize_hex",
    "coerce_bool",
    "coerce_int",
    "coerce_uint",
    "coerce_bytes",
    "coerce_address",
    "IntType",
    "UIntType",
    "BytesType",
    "BoolType",
    "AddressType",
    "parse_type",
]

# ──────────────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────────────


class ABITypeError(TypeError):
    """Raised when an ABI type spec is malformed or unsupported."""


class ValidationError(ValueError):
    """Raised when a Python value does not conform to an ABI type."""


# ──────────────────────────────────────────────────────────────────────────────
# Address helpers (prefer pq bech32m utils; fall back to a light validator)
# ──────────────────────────────────────────────────────────────────────────────

Address = NewType("Address", str)


def _try_bech32m_decode(addr: str) -> Tuple[str, bytes] | None:
    """
    Try to decode with pq's bech32m helper if available.
    Returns (hrp, data) on success, or None if helper not present or decode fails.
    """
    try:
        # Preferred path (provided by pq module in this repo)
        from pq.py.utils.bech32 import bech32m_decode  # type: ignore

        hrp, data = bech32m_decode(addr)
        if hrp is None or data is None:
            return None
        return hrp, bytes(data)
    except Exception:
        return None


def is_address(addr: str, *, hrp: str = "anim") -> bool:
    """Heuristically validate an Animica address (bech32m, 'anim' HRP by default)."""
    if not isinstance(addr, str):
        return False
    decoded = _try_bech32m_decode(addr)
    if decoded is not None:
        dhrp, payload = decoded
        return (
            dhrp == hrp and 4 <= len(payload) <= 1024
        )  # loose bounds; precise rules in pq
    # Light fallback: basic bech32 charset/structure check and HRP prefix.
    # This is *not* a full bech32m check—only used when pq utils are absent.
    if not addr.lower().startswith(hrp + "1"):
        return False
    charset = set("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
    try:
        pos = addr.rindex("1")
    except ValueError:
        return False
    if pos < 1 or pos + 7 > len(addr):  # need separator + 6-char checksum at minimum
        return False
    data = addr[pos + 1 :].lower()
    return all(c in charset for c in data)


def coerce_address(value: Any, *, hrp: str = "anim") -> Address:
    """Accept a bech32m string and ensure it looks like a valid Animica address."""
    if not isinstance(value, str):
        raise ValidationError("address must be a string (bech32m)")
    if not is_address(value, hrp=hrp):
        raise ValidationError(f"invalid address (expected {hrp} bech32m)")
    return Address(value)


# ──────────────────────────────────────────────────────────────────────────────
# Scalar coercion helpers
# ──────────────────────────────────────────────────────────────────────────────


def normalize_hex(s: str) -> bytes:
    """Convert a 0x-prefixed hex string to bytes, accepting even-length only."""
    if not isinstance(s, str) or not s.startswith("0x"):
        raise ValidationError("expected 0x-prefixed hex string")
    hex_part = s[2:]
    if len(hex_part) % 2 != 0:
        raise ValidationError("hex string must have an even number of digits")
    try:
        return bytes.fromhex(hex_part)
    except ValueError as e:
        raise ValidationError(f"invalid hex: {e}") from e


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ValidationError("bool must be True/False")


def coerce_int(value: Any, *, bits: int = 256, signed: bool = True) -> int:
    if not isinstance(value, int):
        raise ValidationError("int must be a Python int")
    if bits <= 0 or bits > 256:
        raise ABITypeError("bits must be in 1..256")
    min_v = -(1 << (bits - 1)) if signed else 0
    max_v = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
    if value < min_v or value > max_v:
        kind = "int" if signed else "uint"
        raise ValidationError(f"{kind}{bits} out of range [{min_v}, {max_v}]")
    return int(value)


def coerce_uint(value: Any, *, bits: int = 256) -> int:
    return coerce_int(value, bits=bits, signed=False)


def coerce_bytes(
    value: Any,
    *,
    fixed_len: Optional[int] = None,
    max_len: Optional[int] = None,
) -> bytes:
    """Accept bytes or 0x-hex; enforce optional fixed or max length (in bytes)."""
    if isinstance(value, bytes):
        b = value
    elif isinstance(value, bytearray):
        b = bytes(value)
    elif isinstance(value, str) and value.startswith("0x"):
        b = normalize_hex(value)
    else:
        raise ValidationError("bytes must be bytes, bytearray, or 0x-hex string")
    if fixed_len is not None and len(b) != fixed_len:
        raise ValidationError(f"bytes length must be exactly {fixed_len}, got {len(b)}")
    if max_len is not None and len(b) > max_len:
        raise ValidationError(f"bytes too long (max {max_len}, got {len(b)})")
    return b


# ──────────────────────────────────────────────────────────────────────────────
# Type specs
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntType:
    bits: int = 256
    signed: bool = True

    def validate(self, value: Any) -> int:
        return coerce_int(value, bits=self.bits, signed=self.signed)

    @property
    def name(self) -> str:
        return f"{'int' if self.signed else 'uint'}{self.bits}"


@dataclass(frozen=True)
class UIntType:
    bits: int = 256

    def validate(self, value: Any) -> int:
        return coerce_uint(value, bits=self.bits)

    @property
    def name(self) -> str:
        return f"uint{self.bits}"


@dataclass(frozen=True)
class BytesType:
    fixed_len: Optional[int] = None
    max_len: Optional[int] = None  # enforced when fixed_len is None

    def __post_init__(self) -> None:
        if self.fixed_len is not None and self.fixed_len < 0:
            raise ABITypeError("fixed_len must be >= 0")
        if self.fixed_len is None and self.max_len is not None and self.max_len <= 0:
            raise ABITypeError("max_len must be > 0")

    def validate(self, value: Any) -> bytes:
        return coerce_bytes(value, fixed_len=self.fixed_len, max_len=self.max_len)

    @property
    def name(self) -> str:
        if self.fixed_len is not None:
            return f"bytes{self.fixed_len}"
        return "bytes"


@dataclass(frozen=True)
class BoolType:
    def validate(self, value: Any) -> bool:
        return coerce_bool(value)

    @property
    def name(self) -> str:
        return "bool"


@dataclass(frozen=True)
class AddressType:
    hrp: str = "anim"

    def validate(self, value: Any) -> Address:
        return coerce_address(value, hrp=self.hrp)

    @property
    def name(self) -> str:
        return "address"


# ──────────────────────────────────────────────────────────────────────────────
# Parser for textual type specs (e.g., "uint256", "bytes32", "address", "bool")
# ──────────────────────────────────────────────────────────────────────────────


def parse_type(spec: str) -> Any:
    """
    Parse a textual type spec into a type object with a .validate() method.
    Supported forms:
      - "int", "intN" where N ∈ {8,16,…,256}
      - "uint", "uintN"
      - "bool"
      - "bytes" (dynamic, length-prefixed in ABI encoding)
      - "bytesN" where 1 ≤ N ≤ 65535 (fixed-length)
      - "address" (bech32m string with HRP 'anim' by default)
    """
    if not isinstance(spec, str) or not spec:
        raise ABITypeError("type spec must be a non-empty string")

    s = spec.strip().lower()

    if s == "bool":
        return BoolType()

    if s == "address":
        return AddressType()

    if s == "int":
        return IntType(bits=256, signed=True)
    if s == "uint":
        return UIntType(bits=256)

    if s.startswith("int"):
        try:
            bits = int(s[3:])
        except ValueError as e:
            raise ABITypeError("invalid int bit width") from e
        _assert_bits(bits)
        return IntType(bits=bits, signed=True)

    if s.startswith("uint"):
        try:
            bits = int(s[4:])
        except ValueError as e:
            raise ABITypeError("invalid uint bit width") from e
        _assert_bits(bits)
        return UIntType(bits=bits)

    if s == "bytes":
        # Dynamic bytes; sane default upper bound applied by encoder if needed.
        return BytesType(fixed_len=None, max_len=None)

    if s.startswith("bytes"):
        try:
            n = int(s[5:])
        except ValueError as e:
            raise ABITypeError("invalid bytesN length") from e
        if n <= 0 or n > 65535:
            raise ABITypeError("bytesN length must be in 1..65535")
        return BytesType(fixed_len=n)

    raise ABITypeError(f"unsupported type spec: {spec!r}")


def _assert_bits(bits: int) -> None:
    if bits not in {
        8,
        16,
        24,
        32,
        40,
        48,
        56,
        64,
        72,
        80,
        88,
        96,
        104,
        112,
        120,
        128,
        136,
        144,
        152,
        160,
        168,
        176,
        184,
        192,
        200,
        208,
        216,
        224,
        232,
        240,
        248,
        256,
    }:
        raise ABITypeError("bit width must be a multiple of 8 in 1..256")
