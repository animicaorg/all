"""
core.utils.bytes
================

Lightweight, dependency-free helpers around byte handling:

- Hex helpers: to_hex/from_hex, 0x-prefix management
- Length guards: ensure_len / ensure_min_len / ensure_max_len
- Integer conversions: int_to_be / be_to_int
- Bytes-like normalization: b(), is_byteslike()
- bech32m codec (BIP-0173/350) with convertbits (8→5 and 5→8)

This module deliberately does **not** import anything outside the stdlib,
so it can be used across the codebase (including very early boot paths).

All bech32 encoding here uses the **bech32m** constant by default, as
required for modern address formats.

Examples
--------
>>> to_hex(b"\\x01\\x02")
'0x0102'
>>> from_hex('0xdeadbeef')
b'\\xde\\xad\\xbe\\xef'
>>> int_to_be(258, length=3)
b'\\x00\\x01\\x02'
>>> be_to_int(b'\\x00\\x01\\x02')
258
>>> addr = bech32m_encode('anim', b'\\x00\\x11\\x22')
>>> hrp, pl = bech32m_decode(addr); (hrp, pl)
('anim', b'\\x00\\x11\\x22')
"""

from __future__ import annotations

from typing import Iterable, Tuple, Union

BytesLike = Union[bytes, bytearray, memoryview]


# -----------------------
# Basic bytes/hex helpers
# -----------------------

def is_byteslike(x: object) -> bool:
    return isinstance(x, (bytes, bytearray, memoryview))


def strip0x(s: str) -> str:
    return s[2:] if s.startswith(("0x", "0X")) else s


def prefix0x(h: str) -> str:
    h = h.lower()
    return h if h.startswith("0x") else "0x" + h


def to_hex(data: BytesLike, *, prefix: bool = True) -> str:
    """Return lowercase hex string of data."""
    if isinstance(data, memoryview):
        data = data.tobytes()
    elif isinstance(data, bytearray):
        data = bytes(data)
    if not isinstance(data, bytes):
        raise TypeError("to_hex expects bytes-like")
    h = data.hex()
    return f"0x{h}" if prefix else h


def from_hex(h: str) -> bytes:
    """Parse hex string with or without 0x prefix; ignores surrounding whitespace."""
    if not isinstance(h, str):
        raise TypeError("from_hex expects str")
    h = strip0x(h.strip().replace(" ", ""))
    if len(h) % 2 == 1:  # pad leading zero if odd length
        h = "0" + h
    try:
        return bytes.fromhex(h)
    except ValueError as e:
        raise ValueError(f"invalid hex string: {e}") from e


def b(x: Union[BytesLike, str, int]) -> bytes:
    """
    Normalize input to bytes:
    - bytes/bytearray/memoryview → bytes
    - str → if startswith '0x' parse hex, else utf-8 encode
    - int → big-endian minimal length
    """
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, memoryview):
        return x.tobytes()
    if isinstance(x, str):
        return from_hex(x) if x.startswith(("0x", "0X")) else x.encode("utf-8")
    if isinstance(x, int):
        if x < 0:
            raise ValueError("b(int): negative not supported")
        length = max(1, (x.bit_length() + 7) // 8)
        return x.to_bytes(length, "big")
    raise TypeError(f"unsupported type for b(): {type(x)!r}")


# ---------------------
# Length/shape guarding
# ---------------------

def ensure_len(data: BytesLike, n: int, *, name: str = "bytes") -> bytes:
    data_b = b(data)
    if len(data_b) != n:
        raise ValueError(f"{name} must be length {n}, got {len(data_b)}")
    return data_b


def ensure_min_len(data: BytesLike, n: int, *, name: str = "bytes") -> bytes:
    data_b = b(data)
    if len(data_b) < n:
        raise ValueError(f"{name} must be at least {n} bytes, got {len(data_b)}")
    return data_b


def ensure_max_len(data: BytesLike, n: int, *, name: str = "bytes") -> bytes:
    data_b = b(data)
    if len(data_b) > n:
        raise ValueError(f"{name} must be at most {n} bytes, got {len(data_b)}")
    return data_b


def expect_len(data: BytesLike, n: int, *, name: str = "bytes") -> bytes:
    """Return ``data`` as immutable bytes after validating exact length ``n``."""
    data_b = b(data)
    if len(data_b) != n:
        raise ValueError(f"{name} must be length {n}, got {len(data_b)}")
    return data_b


# -------------------------
# Integer ↔ big-endian bytes
# -------------------------

def int_to_be(x: int, *, length: int | None = None) -> bytes:
    if x < 0:
        raise ValueError("int_to_be: negative not supported")
    if length is None:
        length = max(1, (x.bit_length() + 7) // 8)
    return x.to_bytes(length, "big")


def be_to_int(data: BytesLike) -> int:
    return int.from_bytes(b(data), "big")


# ---------------
# bech32m codec
# ---------------

# Ref: BIP-0173 & BIP-0350
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_CHARSET_REV = {c: i for i, c in enumerate(_BECH32_CHARSET)}
# polymod constants
_GENERATORS = (0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)
# Checksums
_CONST_BECH32 = 1
_CONST_BECH32M = 0x2bc830a3


def _hrp_expand(hrp: str) -> Iterable[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _polymod(values: Iterable[int]) -> int:
    chk = 1
    for v in values:
        b_ = (chk >> 25) & 0xFF
        chk = ((chk & 0x1ffffff) << 5) ^ v
        for i in range(5):
            chk ^= _GENERATORS[i] if ((b_ >> i) & 1) else 0
    return chk


def _create_checksum(hrp: str, data: Iterable[int], const: int) -> Iterable[int]:
    pm = _polymod(list(_hrp_expand(hrp)) + list(data) + [0, 0, 0, 0, 0, 0]) ^ const
    return [(pm >> 5 * (5 - i)) & 31 for i in range(6)]


def _verify_checksum(hrp: str, data: Iterable[int], const: int) -> bool:
    return _polymod(list(_hrp_expand(hrp)) + list(data)) == const


def _check_hrp(hrp: str) -> None:
    if not (1 <= len(hrp) <= 83):
        raise ValueError("HRP length must be 1..83")
    for c in hrp:
        oc = ord(c)
        if oc < 33 or oc > 126:
            raise ValueError("HRP contains invalid character")


def _convertbits(data: Iterable[int], from_bits: int, to_bits: int, pad: bool) -> Tuple[bytes, bool]:
    """General power-of-2 base conversion. Returns (out, success)."""
    acc = 0
    bits = 0
    ret = bytearray()
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or (value >> from_bits):
            return b"", False
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        return b"", False
    return bytes(ret), True


def bech32m_encode(hrp: str, payload: bytes, *, limit: int = 90) -> str:
    """
    Encode bytes payload as bech32m string with given hrp.
    Payload is converted 8→5 bits per BIP-0173.
    """
    _check_hrp(hrp)
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload must be bytes-like")
    data5, ok = _convertbits(payload, 8, 5, pad=True)
    if not ok:
        raise ValueError("convertbits 8→5 failed")
    data5_list = list(data5)
    checksum = list(_create_checksum(hrp, data5_list, _CONST_BECH32M))
    combined = data5_list + checksum
    # assemble
    out = hrp + "1" + "".join(_BECH32_CHARSET[d] for d in combined)
    if len(out) > limit:
        raise ValueError(f"bech32m string too long ({len(out)}>{limit})")
    return out


def bech32m_decode(s: str) -> Tuple[str, bytes]:
    """Decode bech32m string, returning (hrp, payload bytes)."""
    if not isinstance(s, str):
        raise TypeError("bech32m_decode expects str")
    if any(ord(c) < 33 or ord(c) > 126 for c in s):
        raise ValueError("invalid character range")
    # mixed-case is forbidden
    if (s.lower() != s) and (s.upper() != s):
        raise ValueError("mixed case not allowed in bech32")
    s = s.lower()
    pos = s.rfind("1")
    if pos < 1 or pos + 7 > len(s):  # need at least 1 char HRP + '1' + 6 checksum
        raise ValueError("separator position invalid")
    hrp = s[:pos]
    _check_hrp(hrp)
    data_part = s[pos + 1:]
    data = []
    try:
        for c in data_part:
            data.append(_CHARSET_REV[c])
    except KeyError:
        raise ValueError("invalid character in data part")
    if not _verify_checksum(hrp, data, _CONST_BECH32M):
        raise ValueError("invalid bech32m checksum")
    # strip checksum
    data_nochk = data[:-6]
    payload, ok = _convertbits(data_nochk, 5, 8, pad=False)
    if not ok:
        raise ValueError("convertbits 5→8 failed")
    return hrp, payload


def bech32_encode(hrp: str, payload5: Iterable[int], *, bech32m: bool = True) -> str:
    """
    Encode given 5-bit values directly (advanced).
    If bech32m=False, use legacy bech32 constant (rarely appropriate).
    """
    _check_hrp(hrp)
    data5 = list(payload5)
    const = _CONST_BECH32M if bech32m else _CONST_BECH32
    checksum = _create_checksum(hrp, data5, const)
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in (data5 + list(checksum)))


def bech32_decode_raw(s: str) -> Tuple[str, list[int]]:
    """
    Decode bech32/bech32m string without converting bits.
    Returns (hrp, data5) **including** checksum (caller decides variant).
    """
    if any(ord(c) < 33 or ord(c) > 126 for c in s):
        raise ValueError("invalid character range")
    if (s.lower() != s) and (s.upper() != s):
        raise ValueError("mixed case not allowed in bech32")
    s = s.lower()
    pos = s.rfind("1")
    if pos < 1 or pos + 7 > len(s):
        raise ValueError("separator position invalid")
    hrp = s[:pos]
    _check_hrp(hrp)
    data = []
    try:
        for c in s[pos + 1:]:
            data.append(_CHARSET_REV[c])
    except KeyError:
        raise ValueError("invalid character in data part")
    return hrp, data


def is_valid_bech32m(s: str, *, expected_hrp: str | None = None) -> bool:
    try:
        hrp, _ = bech32m_decode(s)
        return expected_hrp is None or hrp == expected_hrp
    except Exception:
        return False


__all__ = [
    "BytesLike",
    "is_byteslike",
    "strip0x",
    "prefix0x",
    "to_hex",
    "from_hex",
    "b",
    "ensure_len",
    "ensure_min_len",
    "ensure_max_len",
    "expect_len",
    "int_to_be",
    "be_to_int",
    "bech32m_encode",
    "bech32m_decode",
    "bech32_encode",
    "bech32_decode_raw",
    "is_valid_bech32m",
]
