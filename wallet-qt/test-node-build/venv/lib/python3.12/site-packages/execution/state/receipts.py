"""
execution.state.receipts — build Receipt objects and compute logs bloom/root.

This module assembles transaction receipts from execution results:
- Computes a 2048-bit logs bloom (Ethereum-style) from addresses/topics.
- Computes a deterministic logs root (Merkle over per-log digests).
- Returns a Receipt-like object that matches core/types/receipt.py when present.

Design notes
------------
* Pure-stdlib (hashlib.sha3_*); no external deps.
* Stable hashing: per-log digest = H(address | uvarint(n_topics) | topics... |
  uvarint(len(data)) | data), where H = SHA3-256 and uvarint is LEB128.
* Merkle root: pairwise SHA3-256 over concatenated child hashes; empty list → H(b"").
* Bloom: 2048-bit (256-byte) big-endian bitset. For each address and topic, derive
  three 11-bit indices from SHA3-256(item), set the bits.

If core.types.receipt.Receipt exists, build_receipt(...) will try to return an
instance of that class. Otherwise it returns a local ReceiptFields dataclass.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

try:
    # Optional dependency on core; will not be available early in bring-up.
    from core.types.receipt import Receipt as CoreReceipt  # type: ignore
except Exception:  # pragma: no cover - optional import
    CoreReceipt = None  # type: ignore[assignment]

from ..types.events import LogEvent

# =============================================================================
# Small helpers
# =============================================================================


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _uvarint(n: int) -> bytes:
    """LEB128 (unsigned) variable-length integer encoding."""
    if n < 0:
        raise ValueError("uvarint expects non-negative integer")
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


def _per_log_digest(log: LogEvent) -> bytes:
    parts = [
        log.address,
        _uvarint(len(log.topics)),
    ]
    for t in log.topics:
        parts.append(_uvarint(len(t)))
        parts.append(t)
    parts.append(_uvarint(len(log.data)))
    parts.append(log.data)
    return _sha3_256(b"".join(parts))


def _merkle_root(hashes: Sequence[bytes]) -> bytes:
    """Canonical binary Merkle root over the sequence of leaf digests."""
    if not hashes:
        return _sha3_256(b"")
    level = list(hashes)
    while len(level) > 1:
        nxt: List[bytes] = []
        it = iter(level)
        for a in it:
            try:
                b = next(it)
            except StopIteration:
                b = a  # duplicate last for odd count
            nxt.append(_sha3_256(a + b))
        level = nxt
    return level[0]


# =============================================================================
# Logs bloom (2048-bit)
# =============================================================================

_BLOOM_BITS = 2048
_BLOOM_BYTES = _BLOOM_BITS // 8


def _bloom_indices(item: bytes) -> tuple[int, int, int]:
    """
    Derive three 11-bit indices from SHA3-256(item).
    Mirrors the common { (h[0:2]), (h[2:4]), (h[4:6]) } & 0x7FF construction.
    """
    h = _sha3_256(item)
    return (
        int.from_bytes(h[0:2], "big") & 0x7FF,
        int.from_bytes(h[2:4], "big") & 0x7FF,
        int.from_bytes(h[4:6], "big") & 0x7FF,
    )


def build_logs_bloom(logs: Sequence[LogEvent]) -> bytes:
    """
    Build a 256-byte (2048-bit) bloom filter for the provided logs.
    Bits are addressed big-endian as in Ethereum: index 0 is the MSB of byte 0.
    """
    bloom = bytearray(_BLOOM_BYTES)

    def set_bit(ix: int) -> None:
        byte_ix = (_BLOOM_BITS - 1 - ix) // 8
        bit_ix = ix % 8
        bloom[byte_ix] |= 1 << bit_ix

    for log in logs:
        # Address contributes
        for idx in _bloom_indices(log.address):
            set_bit(idx)
        # Each topic contributes
        for topic in log.topics:
            for idx in _bloom_indices(topic):
                set_bit(idx)
    return bytes(bloom)


# =============================================================================
# Receipt assembly
# =============================================================================


@dataclass(frozen=True)
class ReceiptFields:
    """
    A minimal, serializable view of a transaction receipt.
    If core.types.receipt.Receipt is available, `build_receipt` returns that
    class instead; otherwise it returns this.
    """

    status: int  # 1 = SUCCESS, 0 = REVERT/OOG (or as defined upstream)
    gas_used: int
    logs_bloom: bytes  # 256 bytes
    logs_root: bytes  # 32 bytes (SHA3-256 Merkle root)
    logs: List[LogEvent]


def build_receipt(*, status: int, gas_used: int, logs: Sequence[LogEvent]) -> Any:
    """
    Construct a Receipt-like object including bloom and logs root.

    Returns:
        core.types.receipt.Receipt if importable; otherwise ReceiptFields.
    """
    # Compute per-log digests and root
    digests = [_per_log_digest(l) for l in logs]
    logs_root = _merkle_root(digests)
    logs_bloom = build_logs_bloom(logs)

    if CoreReceipt is not None:
        # Try common constructor shapes; be permissive about field names.
        try:
            return CoreReceipt(
                status=status,
                gas_used=gas_used,
                logs=tuple(logs),
                logs_bloom=logs_bloom,
                logs_root=logs_root,
            )
        except TypeError:
            # Fallback try with camelCase names some codebases prefer.
            try:
                return CoreReceipt(
                    status=status,
                    gasUsed=gas_used,  # type: ignore[arg-type]
                    logs=tuple(logs),
                    logsBloom=logs_bloom,  # type: ignore[arg-type]
                    logsRoot=logs_root,  # type: ignore[arg-type]
                )
            except Exception:
                # Last resort: return ReceiptFields to keep execution flowing.
                pass

    return ReceiptFields(
        status=status,
        gas_used=gas_used,
        logs_bloom=logs_bloom,
        logs_root=logs_root,
        logs=list(logs),
    )


__all__ = [
    "ReceiptFields",
    "build_receipt",
    "build_logs_bloom",
]
