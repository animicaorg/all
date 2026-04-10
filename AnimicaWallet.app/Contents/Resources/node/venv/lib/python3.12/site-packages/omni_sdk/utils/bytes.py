from __future__ import annotations

from typing import Tuple, Union

BytesLike = Union[bytes, bytearray, memoryview]


def ensure_bytes(data: Union[BytesLike, str]) -> bytes:
    """
    Ensure input is bytes.

    Accepts:
      - bytes / bytearray / memoryview  -> bytes(data)
      - str: treated as hex; optional '0x' prefix; even-length enforced

    Raises:
      ValueError on invalid hex strings.
    """
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if isinstance(data, str):
        return from_hex(data)
    raise TypeError(f"Unsupported type for ensure_bytes: {type(data)!r}")


def to_hex(b: BytesLike, prefix: bool = True) -> str:
    """
    Bytes -> hex string (lowercase). Prefix with '0x' by default.
    """
    s = bytes(b).hex()
    return f"0x{s}" if prefix else s


def from_hex(s: str) -> bytes:
    """
    Hex string (optionally '0x' prefixed) -> bytes.

    Enforces even-length (nibbles must pair to bytes) and lowercase/uppercase agnostic.
    """
    if not isinstance(s, str):
        raise TypeError("from_hex expects a string")
    if s.startswith(("0x", "0X")):
        s = s[2:]
    if len(s) % 2 != 0:
        # allow odd-length by left-padding a zero nibble? Prefer strictness here.
        raise ValueError("hex string must have even length")
    try:
        return bytes.fromhex(s)
    except ValueError as e:
        raise ValueError(f"invalid hex string: {e}") from e


# --- Unsigned varint (LEB128) -------------------------------------------------


def uvarint_encode(n: int) -> bytes:
    """
    Encode an unsigned integer using LEB128 (base-128 varint).

    - Non-negative integers only.
    - Little-endian groups of 7 bits; MSB continuation bit.

    Example:
        0x00 -> b'\\x00'
        0x7f -> b'\\x7f'
        0x80 -> b'\\x80\\x01'
    """
    if n < 0:
        raise ValueError("uvarint_encode expects a non-negative integer")
    out = bytearray()
    while True:
        to_write = n & 0x7F
        n >>= 7
        if n:
            out.append(to_write | 0x80)  # set continuation bit
        else:
            out.append(to_write)
            break
    return bytes(out)


def uvarint_decode(b: BytesLike, *, offset: int = 0) -> Tuple[int, int]:
    """
    Decode an unsigned LEB128 varint from bytes starting at `offset`.

    Returns:
        (value, length_consumed)

    Raises:
        ValueError if the varint is malformed or overflows 64-bit (guardrail).

    Notes:
        - We allow values up to 2**64-1 to prevent unbounded shifts in hostile inputs.
        - Caller can pass the returned consumed length to continue parsing a stream.
    """
    result = 0
    shift = 0
    consumed = 0
    b = memoryview(b)[offset:].tobytes()

    for consumed, byte in enumerate(b, start=1):
        result |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            # terminal byte
            return result, consumed
        shift += 7
        if shift >= 64:  # guardrail to avoid unbounded growth
            raise ValueError("uvarint too large (exceeds 64 bits)")
    # If we exit the loop without returning, input ended mid-varint
    raise ValueError("truncated uvarint (input ended before termination byte)")


__all__ = [
    "BytesLike",
    "ensure_bytes",
    "to_hex",
    "from_hex",
    "uvarint_encode",
    "uvarint_decode",
]
