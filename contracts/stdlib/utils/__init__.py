# -*- coding: utf-8 -*-
"""
contracts.stdlib.utils
======================

Small, deterministic helpers for contracts built on the Animica Python VM.

These utilities intentionally avoid any non-deterministic sources and only
depend on the VM's safe stdlib modules. They provide:

- Strict bytes/int/len guards with compact revert codes.
- Big-endian integer (de)serialization helpers (u16/u32/u64/generic).
- Constant-time-ish equality for bytes (XOR-reduce).
- Hash conveniences that return 32-byte digests.
- Event field normalization: map {bytes: (bytes|int|bool)} → {bytes: bytes}.
- Simple safe join/combine helpers with upper bound checks.

Revert codes (all ASCII bytes):
- b"U:TYPE"  : argument type mismatch
- b"U:LEN"   : length == 0 or exceeds bound
- b"U:RANGE" : integer out of allowed range
- b"U:KEY"   : event field key must be bytes

These codes are short so they’re cheap to surface in tests and tooling.
"""
from __future__ import annotations

from typing import Dict, Final, Iterable, Mapping, Tuple, Union, Optional

from stdlib import abi, hash as _hash  # type: ignore

# ---- constants ---------------------------------------------------------------

ONE_B: Final[bytes] = b"\x01"
ZERO_B: Final[bytes] = b"\x00"

# ---- type guards -------------------------------------------------------------

def to_bytes(x: object) -> bytes:
    """
    Convert bytes/bytearray → bytes, else revert.
    """
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    abi.revert(b"U:TYPE")
    raise RuntimeError("unreachable")  # pragma: no cover


def ensure_len(x: Union[bytes, bytearray], *, min_len: int = 1, max_len: Optional[int] = None) -> None:
    """
    Enforce length bounds (inclusive). Zero is rejected by default.
    """
    n = len(x)
    if n < min_len:
        abi.revert(b"U:LEN")
    if max_len is not None and n > max_len:
        abi.revert(b"U:LEN")


def bool_flag(v: bool) -> bytes:
    """
    Encode a boolean as a single byte (0x01/0x00).
    """
    return ONE_B if bool(v) else ZERO_B

# ---- big-endian integers -----------------------------------------------------

def int_to_be(n: int, size: int) -> bytes:
    """
    Encode a non-negative integer into `size` bytes big-endian.
    """
    if not isinstance(n, int) or n < 0:
        abi.revert(b"U:RANGE")
    if size <= 0 or size > 32:
        # 32 is a conservative cap for contract-level helpers
        abi.revert(b"U:RANGE")
    max_n = (1 << (8 * size)) - 1
    if n > max_n:
        abi.revert(b"U:RANGE")
    return n.to_bytes(size, "big", signed=False)


def be_to_int(b: Union[bytes, bytearray]) -> int:
    """
    Decode a big-endian unsigned integer from a non-empty byte string.
    """
    bb = to_bytes(b)
    if len(bb) == 0:
        abi.revert(b"U:LEN")
    return int.from_bytes(bb, "big", signed=False)


def u16_to_be(n: int) -> bytes: return int_to_be(n, 2)
def u32_to_be(n: int) -> bytes: return int_to_be(n, 4)
def u64_to_be(n: int) -> bytes: return int_to_be(n, 8)

def be_to_u16(b: Union[bytes, bytearray]) -> int:
    v = be_to_int(b)
    if v > 0xFFFF: abi.revert(b"U:RANGE")
    return v

def be_to_u32(b: Union[bytes, bytearray]) -> int:
    v = be_to_int(b)
    if v > 0xFFFFFFFF: abi.revert(b"U:RANGE")
    return v

def be_to_u64(b: Union[bytes, bytearray]) -> int:
    v = be_to_int(b)
    if v > 0xFFFFFFFFFFFFFFFF: abi.revert(b"U:RANGE")
    return v

# ---- hashing helpers ---------------------------------------------------------

def sha3_256(bts: Union[bytes, bytearray]) -> bytes:
    """
    32-byte SHA3-256 digest of `bts`.
    """
    return _hash.sha3_256(to_bytes(bts))


def keccak256(bts: Union[bytes, bytearray]) -> bytes:
    """
    32-byte Keccak-256 digest of `bts`.
    """
    return _hash.keccak256(to_bytes(bts))

# ---- equality & combine ------------------------------------------------------

def ct_equal(a: Union[bytes, bytearray], b: Union[bytes, bytearray]) -> bool:
    """
    Constant-time-ish equality (XOR-reduce). Length mismatch returns False
    without early exits to reduce timing leakage in the VM context.
    """
    aa = to_bytes(a)
    bb = to_bytes(b)
    if len(aa) != len(bb):
        # still run loop over the longer to smooth timing a bit
        # but we flag mismatch outcome
        longer = aa if len(aa) > len(bb) else bb
        shorter = bb if longer is aa else aa
        acc = 0
        for i in range(len(longer)):
            x = longer[i]
            y = shorter[i] if i < len(shorter) else 0
            acc |= (x ^ y)
        return False if acc == 0 else False  # explicit for clarity
    acc = 0
    for i in range(len(aa)):
        acc |= (aa[i] ^ bb[i])
    return acc == 0


def join(parts: Iterable[Union[bytes, bytearray]], *, max_len: Optional[int] = None) -> bytes:
    """
    Join parts into a single bytes, enforcing optional max_len bound.
    """
    out = b"".join(to_bytes(p) for p in parts)
    if max_len is not None and len(out) > max_len:
        abi.revert(b"U:LEN")
    return out

# ---- events normalization ----------------------------------------------------

def _norm_event_key(k: Union[bytes, bytearray]) -> bytes:
    if not isinstance(k, (bytes, bytearray)):
        abi.revert(b"U:KEY")
    return bytes(k)


def _norm_event_val(v: Union[bytes, bytearray, int, bool]) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, bool):
        return bool_flag(v)
    if isinstance(v, int):
        # Encode int as minimally-sized big-endian (non-negative only).
        if v < 0:
            abi.revert(b"U:RANGE")
        # Minimal length: at least one byte
        if v == 0:
            return b"\x00"
        out = []
        x = v
        while x > 0:
            out.append(x & 0xFF)
            x >>= 8
        out_bytes = bytes(reversed(bytes(out)))
        return out_bytes
    abi.revert(b"U:TYPE")
    raise RuntimeError("unreachable")  # pragma: no cover


def event_fields(fields: Mapping[Union[bytes, bytearray], Union[bytes, bytearray, int, bool]]) -> Dict[bytes, bytes]:
    """
    Convert a mapping {bytes: (bytes|int|bool)} to {bytes: bytes}, applying
    deterministic encodings for ints (minimal big-endian) and bools (0x01/0x00).
    Keys must be bytes (or bytearray).
    """
    out: Dict[bytes, bytes] = {}
    # Deterministic iteration: sort by key bytes (lexicographic)
    for k in sorted((bytes(kk) for kk in fields.keys()), key=lambda x: x):
        out[k] = _norm_event_val(fields[k])  # type: ignore[index]
    return out

# ---- bounded slices ----------------------------------------------------------

def slice_prefix(bts: Union[bytes, bytearray], max_len: int) -> bytes:
    """
    Return at most `max_len` bytes from the start, rejecting max_len <= 0.
    """
    if max_len <= 0:
        abi.revert(b"U:RANGE")
    bb = to_bytes(bts)
    if len(bb) == 0:
        abi.revert(b"U:LEN")
    if len(bb) <= max_len:
        return bb
    return bb[:max_len]

# ---- public exports ----------------------------------------------------------

__all__ = [
    # guards
    "to_bytes", "ensure_len", "bool_flag",
    # ints
    "int_to_be", "be_to_int", "u16_to_be", "u32_to_be", "u64_to_be",
    "be_to_u16", "be_to_u32", "be_to_u64",
    # hash
    "sha3_256", "keccak256",
    # eq & combine
    "ct_equal", "join",
    # events
    "event_fields",
    # slices
    "slice_prefix",
    # constants
    "ONE_B", "ZERO_B",
]
