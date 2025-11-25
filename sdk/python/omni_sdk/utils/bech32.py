"""
Bech32 / Bech32m codec (BIP-0173 / BIP-0350), plus simple address helpers.

This module provides a tiny self-contained implementation so the Python SDK
doesn't depend on external bech32 libraries. It supports both classic Bech32
(constant 1) and Bech32m (constant 0x2bc830a3). For Animica addresses we use
**Bech32m** by default.

Typical usage
-------------
>>> payload = bytes.fromhex("aabbcc")
>>> addr = encode_bytes("anim", payload)   # bech32m by default
>>> hrp, out, spec = decode_bytes(addr)
>>> assert hrp == "anim" and out == payload and spec == "bech32m"

Helpers
-------
- encode(hrp, data5, spec="bech32m") -> string (data must be 5-bit ints 0..31)
- decode(addr) -> (hrp, data5, spec)   (data5 is list[int])
- encode_bytes(hrp, payload, spec="bech32m") -> string (8→5 convertbits)
- decode_bytes(addr, expected_hrp=None) -> (hrp, payload: bytes, spec)
- is_valid_address(addr, expected_hrp=None) -> bool

Notes
-----
- HRP is validated to be lowercase alphanumeric (per spec we enforce lowercase).
- We reject mixed-case and non-canonical forms.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple, Optional

__all__ = [
    "encode",
    "decode",
    "encode_bytes",
    "decode_bytes",
    "convertbits",
    "is_valid_address",
    "Bech32Error",
    "DEFAULT_HRP",
]

DEFAULT_HRP = "anim"  # Animica default HRP

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CHARSET_REV = {c: i for i, c in enumerate(CHARSET)}

_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


class Bech32Error(ValueError):
    pass


def _polymod(values: Sequence[int]) -> int:
    """Internal bech32 polymod checksum."""
    GENERATORS = (0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)
    chk = 1
    for v in values:
        b = (chk >> 25) & 0xFF
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            chk ^= GENERATORS[i] if ((b >> i) & 1) else 0
    return chk


def _hrp_expand(hrp: str) -> List[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _create_checksum(hrp: str, data: Sequence[int], const: int) -> List[int]:
    values = _hrp_expand(hrp) + list(data)
    pm = _polymod(values + [0, 0, 0, 0, 0, 0]) ^ const
    return [(pm >> 5 * (5 - i)) & 31 for i in range(6)]


def _verify_checksum(hrp: str, data: Sequence[int]) -> str:
    """Return 'bech32' or 'bech32m' if checksum matches, else raise."""
    check = _polymod(_hrp_expand(hrp) + list(data))
    if check == _BECH32_CONST:
        return "bech32"
    if check == _BECH32M_CONST:
        return "bech32m"
    raise Bech32Error("invalid checksum")


def _validate_hrp(hrp: str) -> None:
    if not hrp or any(not ("a" <= c <= "z" or "0" <= c <= "9") for c in hrp):
        # Strict: lowercase alnum only; keeps UX simple and canonical.
        raise Bech32Error("invalid HRP (must be lowercase alphanumeric)")


def encode(hrp: str, data5: Iterable[int], *, spec: str = "bech32m") -> str:
    """
    Encode to bech32/bech32m. `data5` must be 5-bit integers (0..31).
    """
    _validate_hrp(hrp)
    data5 = list(data5)
    if any((v < 0 or v > 31) for v in data5):
        raise Bech32Error("data5 values must be in 0..31")
    const = _BECH32M_CONST if spec == "bech32m" else _BECH32_CONST
    checksum = _create_checksum(hrp, data5, const)
    encoded = hrp + "1" + "".join(CHARSET[d] for d in (list(data5) + checksum))
    return encoded


def decode(addr: str) -> Tuple[str, List[int], str]:
    """
    Decode bech32/bech32m string. Returns (hrp, data5, spec).
    Raises Bech32Error on failure.
    """
    if any(ord(x) < 33 or ord(x) > 126 for x in addr):
        raise Bech32Error("invalid characters")
    if addr.lower() != addr and addr.upper() != addr:
        raise Bech32Error("mixed case not allowed")
    addr = addr.lower()
    if addr.rfind("1") == -1:
        raise Bech32Error("missing separator '1'")
    pos = addr.rfind("1")
    hrp, rest = addr[:pos], addr[pos + 1 :]
    _validate_hrp(hrp)
    if len(rest) < 6:
        raise Bech32Error("too short data/checksum")
    try:
        data = [CHARSET_REV[c] for c in rest]
    except KeyError:
        raise Bech32Error("invalid charset") from None
    spec = _verify_checksum(hrp, data)
    return hrp, data[:-6], spec


def convertbits(data: Iterable[int], from_bits: int, to_bits: int, *, pad: bool = True) -> List[int]:
    """
    General power-of-two base conversion (e.g., 8→5 or 5→8).
    Returns list of integers in the target base.
    """
    acc = 0
    bits = 0
    ret: List[int] = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            raise Bech32Error("invalid value for convertbits")
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    else:
        if bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
            raise Bech32Error("non-zero padding")
    return ret


def encode_bytes(hrp: str, payload: bytes, *, spec: str = "bech32m") -> str:
    """
    Convenience: 8-bit payload → Bech32/Bech32m string via 8→5 conversion.
    """
    data5 = convertbits(payload, 8, 5, pad=True)
    return encode(hrp, data5, spec=spec)


def decode_bytes(addr: str, *, expected_hrp: Optional[str] = None) -> Tuple[str, bytes, str]:
    """
    Decode an address produced by `encode_bytes`. Validates checksum and (optionally) HRP.
    Returns (hrp, payload_bytes, spec).
    """
    hrp, data5, spec = decode(addr)
    if expected_hrp is not None and hrp != expected_hrp:
        raise Bech32Error(f"HRP mismatch: expected {expected_hrp}, got {hrp}")
    data8 = convertbits(data5, 5, 8, pad=False)
    return hrp, bytes(data8), spec


def is_valid_address(addr: str, expected_hrp: Optional[str] = None) -> bool:
    try:
        h, _, spec = decode(addr)
        if expected_hrp is not None and h != expected_hrp:
            return False
        # For Animica we expect bech32m; tolerate classic if caller wants any.
        if expected_hrp is None:
            return spec in ("bech32m", "bech32")
        return True
    except Bech32Error:
        return False
