"""Canonical, unambiguous binary encoding for L2 transactions and batches.

Design rules (every one is security-relevant — a second valid encoding of the
same logical tx is a replay/equivocation vector):

* Fixed field order, no optional trailing fields, no self-describing tags beyond
  the explicit ``tx_type``.
* Unsigned LEB128 varints for all integers; the encoder emits the *shortest*
  representation and the decoder *rejects* any over-long varint. This is what
  makes the mapping bijective.
* Length-prefixed byte strings; the decoder rejects trailing bytes.
* All addresses/hashes are exactly ``ADDR_LEN``/``HASH_LEN`` — never
  length-prefixed — so their boundaries are unambiguous.
* Decoding is strict: any structural violation raises :class:`CodecError`. There
  is no lenient path.

The signing preimage (:mod:`l2.tx`) is built from the same field bytes as the
wire body *excluding* the signature, so "sign one thing, broadcast another" is
impossible by construction.
"""

from __future__ import annotations

from typing import List, Tuple

from .constants import (
    ADDR_LEN,
    HASH_LEN,
    MAX_AMOUNT,
    MAX_BATCH_PAYMENT_RECIPIENTS,
    MAX_MEMO_BYTES,
    MAX_TX_BYTES,
    PUBKEY_LEN,
    SIG_LEN,
)


class CodecError(ValueError):
    """Raised on any encoding/decoding violation. Never swallowed silently."""


# ── primitive writers ────────────────────────────────────────────────────────


def write_uvarint(out: bytearray, value: int) -> None:
    if value < 0:
        raise CodecError(f"uvarint must be non-negative, got {value}")
    # Bound so a hostile stream cannot make us allocate unboundedly and so the
    # value fits the fixed-width reasoning the executor relies on.
    if value > 2**256:
        raise CodecError("uvarint exceeds 256-bit ceiling")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return


def read_uvarint(buf: memoryview, pos: int) -> Tuple[int, int]:
    """Return (value, new_pos). Rejects over-long (non-minimal) encodings, which
    is what keeps the codec bijective."""
    result = 0
    shift = 0
    start = pos
    while True:
        if pos >= len(buf):
            raise CodecError("truncated uvarint")
        if shift > 252:
            raise CodecError("uvarint too long (>256-bit)")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            # Minimal-encoding check: a multi-byte varint whose final byte is 0
            # could have been shorter. Reject it.
            if pos - start > 1 and byte == 0:
                raise CodecError("non-minimal uvarint encoding")
            return result, pos
        shift += 7


def write_bytes(out: bytearray, data: bytes) -> None:
    write_uvarint(out, len(data))
    out.extend(data)


def read_bytes(buf: memoryview, pos: int, max_len: int) -> Tuple[bytes, int]:
    length, pos = read_uvarint(buf, pos)
    if length > max_len:
        raise CodecError(f"byte string length {length} exceeds max {max_len}")
    end = pos + length
    if end > len(buf):
        raise CodecError("truncated byte string")
    return bytes(buf[pos:end]), end


def write_fixed(out: bytearray, data: bytes, expected: int) -> None:
    if len(data) != expected:
        raise CodecError(f"fixed field must be {expected} bytes, got {len(data)}")
    out.extend(data)


def read_fixed(buf: memoryview, pos: int, size: int) -> Tuple[bytes, int]:
    end = pos + size
    if end > len(buf):
        raise CodecError("truncated fixed field")
    return bytes(buf[pos:end]), end


def write_amount(out: bytearray, amount: int) -> None:
    if amount < 0:
        raise CodecError("amount must be non-negative")
    if amount > MAX_AMOUNT:
        raise CodecError(f"amount {amount} exceeds MAX_AMOUNT")
    write_uvarint(out, amount)


def read_amount(buf: memoryview, pos: int) -> Tuple[int, int]:
    value, pos = read_uvarint(buf, pos)
    if value > MAX_AMOUNT:
        raise CodecError(f"amount {value} exceeds MAX_AMOUNT")
    return value, pos


def write_addr(out: bytearray, addr: bytes) -> None:
    write_fixed(out, addr, ADDR_LEN)


def read_addr(buf: memoryview, pos: int) -> Tuple[bytes, int]:
    return read_fixed(buf, pos, ADDR_LEN)


def write_hash(out: bytearray, h: bytes) -> None:
    write_fixed(out, h, HASH_LEN)


def read_hash(buf: memoryview, pos: int) -> Tuple[bytes, int]:
    return read_fixed(buf, pos, HASH_LEN)


def write_memo(out: bytearray, memo: bytes) -> None:
    if len(memo) > MAX_MEMO_BYTES:
        raise CodecError(f"memo {len(memo)}B exceeds {MAX_MEMO_BYTES}B")
    write_bytes(out, memo)


def read_memo(buf: memoryview, pos: int) -> Tuple[bytes, int]:
    return read_bytes(buf, pos, MAX_MEMO_BYTES)


def write_pubkey(out: bytearray, pk: bytes) -> None:
    write_fixed(out, pk, PUBKEY_LEN)


def read_pubkey(buf: memoryview, pos: int) -> Tuple[bytes, int]:
    return read_fixed(buf, pos, PUBKEY_LEN)


def write_sig(out: bytearray, sig: bytes) -> None:
    write_fixed(out, sig, SIG_LEN)


def read_sig(buf: memoryview, pos: int) -> Tuple[bytes, int]:
    return read_fixed(buf, pos, SIG_LEN)


# ── recipient list (BATCH_PAYMENT) ───────────────────────────────────────────


def write_recipients(out: bytearray, pairs: List[Tuple[bytes, int]]) -> None:
    if len(pairs) > MAX_BATCH_PAYMENT_RECIPIENTS:
        raise CodecError("too many batch-payment recipients")
    write_uvarint(out, len(pairs))
    for addr, amount in pairs:
        write_addr(out, addr)
        write_amount(out, amount)


def read_recipients(buf: memoryview, pos: int) -> Tuple[List[Tuple[bytes, int]], int]:
    count, pos = read_uvarint(buf, pos)
    if count > MAX_BATCH_PAYMENT_RECIPIENTS:
        raise CodecError("too many batch-payment recipients")
    pairs: List[Tuple[bytes, int]] = []
    for _ in range(count):
        addr, pos = read_addr(buf, pos)
        amount, pos = read_amount(buf, pos)
        pairs.append((addr, amount))
    return pairs, pos


# ── whole-buffer guards ──────────────────────────────────────────────────────


def ensure_consumed(buf: memoryview, pos: int) -> None:
    """After decoding a self-contained object the whole buffer must be used;
    trailing bytes would be a second channel for equivocation."""
    if pos != len(buf):
        raise CodecError(f"trailing bytes: consumed {pos} of {len(buf)}")


def guard_tx_size(data: bytes) -> None:
    if len(data) > MAX_TX_BYTES:
        raise CodecError(f"encoded tx {len(data)}B exceeds {MAX_TX_BYTES}B")
    if len(data) == 0:
        raise CodecError("empty tx buffer")
