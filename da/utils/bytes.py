"""
Animica • DA utilities — Byte, hex and varuint helpers

This module centralizes a few low-level, allocation-conscious utilities:

  • Hex helpers with strict "0x" lowercase prefix
  • Chunking/unchunking helpers for large blobs
  • Unsigned LEB128 varuint encode/decode
  • Length-prefixed (varbytes) pack/unpack helpers

All functions are deterministic and side-effect free.
"""
from __future__ import annotations

from typing import Iterable, Iterator, Tuple, Union, BinaryIO

BytesLike = Union[bytes, bytearray, memoryview]

HEX_PREFIX = "0x"


# -----------------------------------------------------------------------------
# Hex helpers
# -----------------------------------------------------------------------------

def add_0x(h: str) -> str:
    """Ensure a lowercase '0x' prefix is present."""
    return h if h.startswith(HEX_PREFIX) else HEX_PREFIX + h


def strip_0x(h: str) -> str:
    """Remove a leading '0x' (if present)."""
    return h[2:] if h.startswith(HEX_PREFIX) else h


def is_hexstr(s: str) -> bool:
    """Return True if s matches ^0x[0-9a-f]*$ (lowercase)."""
    if not s.startswith(HEX_PREFIX):
        return False
    hexpart = s[2:]
    return all(ch in "0123456789abcdef" for ch in hexpart)


def bytes_to_hex(b: BytesLike) -> str:
    """Return '0x' + lowercase hex for the given bytes."""
    bb = _b(b)
    return HEX_PREFIX + bb.hex()


def hex_to_bytes(s: str) -> bytes:
    """
    Parse a strict lowercase hex string with '0x' prefix.
    Raises ValueError on malformed input or odd-length hex.
    """
    if not is_hexstr(s):
        raise ValueError("hex string must match ^0x[0-9a-f]*$")
    hexpart = s[2:]
    if len(hexpart) % 2 != 0:
        raise ValueError("hex payload length must be even")
    return bytes.fromhex(hexpart)


# -----------------------------------------------------------------------------
# Unsigned varints (LEB128-style)
# -----------------------------------------------------------------------------

def write_uvarint(value: int) -> bytes:
    """Encode a non-negative integer using unsigned LEB128."""
    if value < 0:
        raise ValueError("uvarint cannot encode negative values")
    out = bytearray()
    v = int(value)
    while True:
        to_write = v & 0x7F
        v >>= 7
        if v:
            out.append(to_write | 0x80)
        else:
            out.append(to_write)
            break
    return bytes(out)


def read_uvarint(buf: BytesLike, offset: int = 0) -> tuple[int, int]:
    """Decode a uvarint from buf starting at offset → (value, new_offset)."""
    mv = memoryview(_b(buf))
    if offset < 0 or offset >= len(mv):
        raise ValueError("offset out of range")
    shift = 0
    result = 0
    i = offset
    while i < len(mv):
        b = mv[i]
        result |= (int(b) & 0x7F) << shift
        i += 1
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 70:
            raise ValueError("uvarint too large")
    raise ValueError("buffer ended before uvarint completed")


# -----------------------------------------------------------------------------
# Chunking helpers
# -----------------------------------------------------------------------------

def iter_chunks(data: BytesLike, size: int, *, start: int = 0) -> Iterator[memoryview]:
    """
    Yield memoryview slices of `data` of at most `size` bytes, starting at `start`.
    Uses zero-copy slicing when `data` supports buffer protocol.
    """
    if size <= 0:
        raise ValueError("chunk size must be > 0")
    mv = memoryview(_b(data))
    if start < 0 or start > len(mv):
        raise ValueError("start offset out of range")
    i = start
    n = len(mv)
    while i < n:
        j = min(i + size, n)
        yield mv[i:j]
        i = j


def chunk_count(length: int, size: int) -> int:
    """Return the number of chunks of `size` needed to cover `length` bytes."""
    if length < 0 or size <= 0:
        raise ValueError("length must be >= 0 and size > 0")
    return (length + size - 1) // size


def join_chunks(chunks: Iterable[BytesLike]) -> bytes:
    """Join an iterable of byte-like chunks into a single bytes object."""
    return b"".join(_b(c) for c in chunks)


# -----------------------------------------------------------------------------
# Varuint (unsigned LEB128)
# -----------------------------------------------------------------------------

def encode_varuint(n: int) -> bytes:
    """
    Encode non-negative integer `n` using unsigned LEB128 (little-endian base-128).
    """
    if n < 0:
        raise ValueError("varuint requires non-negative integer")
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def decode_varuint(buf: BytesLike, *, offset: int = 0, max_bytes: int = 10) -> Tuple[int, int]:
    """
    Decode a varuint from `buf` starting at `offset`.

    Args:
        buf:     byte buffer containing the encoded varuint.
        offset:  starting index.
        max_bytes: safety cap on the number of bytes to consume (default 10 ~ u64).

    Returns:
        (value, next_offset)

    Raises:
        ValueError on malformed encoding or bounds violations.
    """
    mv = memoryview(_b(buf))
    n = 0
    shift = 0
    i = offset
    consumed = 0
    while True:
        if i >= len(mv):
            raise ValueError("unexpected end of buffer while decoding varuint")
        if consumed >= max_bytes:
            raise ValueError("varuint exceeds safety limit (max_bytes)")
        b = mv[i]
        i += 1
        consumed += 1
        n |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 63 and max_bytes <= 10:
            # If using default cap, avoid pathological expansions.
            raise ValueError("varuint too large (shift overflow)")
    return n, i


def pack_varbytes(payload: BytesLike) -> bytes:
    """
    Pack as varbytes: varuint(len(payload)) || payload.
    """
    pb = _b(payload)
    return encode_varuint(len(pb)) + pb


def unpack_varbytes(buf: BytesLike, *, offset: int = 0, max_len: int | None = None) -> Tuple[bytes, int]:
    """
    Unpack varbytes from `buf` starting at `offset`.

    Returns:
        (payload_bytes, next_offset)

    Raises:
        ValueError if declared length exceeds bounds or buffer.
    """
    length, i = decode_varuint(buf, offset=offset)
    if length < 0:
        raise ValueError("negative length encountered (impossible)")
    if max_len is not None and length > max_len:
        raise ValueError("varbytes length exceeds maximum allowed")
    mv = memoryview(_b(buf))
    j = i + length
    if j > len(mv):
        raise ValueError("buffer too short for declared varbytes length")
    return bytes(mv[i:j]), j


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------

def _b(x: BytesLike) -> bytes:
    """Coerce to `bytes` without unnecessary copies."""
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, memoryview):
        return x.tobytes()
    # Fallback for other buffer protocol implementors
    return bytes(x)  # type: ignore[arg-type]


__all__ = [
    # hex
    "HEX_PREFIX", "add_0x", "strip_0x", "is_hexstr", "bytes_to_hex", "hex_to_bytes",
    # chunking
    "iter_chunks", "chunk_count", "join_chunks",
    # varuint / varbytes
    "encode_varuint", "decode_varuint", "pack_varbytes", "unpack_varbytes",
]
