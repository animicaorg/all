from __future__ import annotations

"""
Transaction Index
=================

A compact, append-friendly index that maps:

  - tx hash (32 bytes) → (height: u64, index: u32, block_hash: 32 bytes)

and a reverse mapping to support fast removal on reorgs and ordered scans:

  - (height: u64, index: u32) → tx hash (32 bytes)

Key layout
----------
- TXI := 0x20 | tx_hash(32)                        -> cbor({ "h": u64, "i": u32, "b": block_hash })
- BTI := 0x21 | height:u64be | idx:u32be           -> tx_hash(32)

Notes
-----
- The forward record carries the block_hash as a convenience for explorers.
- All writes are safe to batch with KV.batch().
- On reorg, call remove_block(height, count) *after* removing the block from the canonical chain.
- The index is canonical-chain only; do not call index_block() unless the block is canonical.

"""

from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

from ..encoding.cbor import cbor_dumps, cbor_loads
from ..utils.bytes import to_hex
from .kv import KV, Batch

# ---------------------------------------------------------------------------
# Constants & key helpers
# ---------------------------------------------------------------------------

PFX_TXI = b"\x20"
PFX_BTI = b"\x21"


def _u64be(n: int) -> bytes:
    if n < 0 or n > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("u64 out of range")
    return n.to_bytes(8, "big")


def _from_u64be(b: bytes) -> int:
    if len(b) != 8:
        raise ValueError("expected 8 bytes for u64")
    return int.from_bytes(b, "big")


def _u32be(n: int) -> bytes:
    if n < 0 or n > 0xFFFFFFFF:
        raise ValueError("u32 out of range")
    return n.to_bytes(4, "big")


def _from_u32be(b: bytes) -> int:
    if len(b) != 4:
        raise ValueError("expected 4 bytes for u32")
    return int.from_bytes(b, "big")


def k_txi(tx_hash: bytes) -> bytes:
    return PFX_TXI + tx_hash


def k_bti(height: int, index: int) -> bytes:
    return PFX_BTI + _u64be(height) + _u32be(index)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TxPointer:
    height: int
    index: int
    block_hash: bytes  # 32 bytes

    def to_cbor(self) -> bytes:
        return cbor_dumps({"h": self.height, "i": self.index, "b": self.block_hash})

    @staticmethod
    def from_cbor(b: bytes) -> "TxPointer":
        d = cbor_loads(b)
        return TxPointer(int(d["h"]), int(d["i"]), bytes(d["b"]))


# ---------------------------------------------------------------------------
# Index API
# ---------------------------------------------------------------------------


class TxIndex:
    """
    Canonical tx index with forward & reverse mappings.
    """

    def __init__(self, kv: KV):
        self.kv = kv

    # --- Writes ---

    def index_block(
        self, height: int, block_hash: bytes, tx_hashes: List[bytes]
    ) -> None:
        """
        Index all txs of a canonical block at `height`. Idempotent for the same data.
        """
        with self.kv.batch() as b:
            for i, th in enumerate(tx_hashes):
                ptr = TxPointer(height=height, index=i, block_hash=block_hash)
                b.put(k_txi(th), ptr.to_cbor())
                b.put(k_bti(height, i), th)
            b.commit()

    # --- Reads ---

    def get(self, tx_hash: bytes) -> Optional[TxPointer]:
        v = self.kv.get(k_txi(tx_hash))
        return None if v is None else TxPointer.from_cbor(v)

    def exists(self, tx_hash: bytes) -> bool:
        return self.kv.get(k_txi(tx_hash)) is not None

    def get_tx_hash_by_pos(self, height: int, index: int) -> Optional[bytes]:
        return self.kv.get(k_bti(height, index))

    def iter_block_tx_hashes(self, height: int) -> Iterator[Tuple[int, bytes]]:
        """
        Iterate (index, tx_hash) for a canonical block's transactions from index=0
        until the first missing index.
        """
        i = 0
        while True:
            th = self.kv.get(k_bti(height, i))
            if th is None:
                break
            yield (i, th)
            i += 1

    def block_tx_count(self, height: int, hard_cap: int = 10_000_000) -> int:
        """
        Count txs in a canonical block by scanning reverse entries from 0..N.
        Stops at the first gap or hard_cap to avoid unbounded scans.
        """
        n = 0
        while n < hard_cap and self.kv.get(k_bti(height, n)) is not None:
            n += 1
        return n

    # --- Deletes (for reorgs) ---

    def remove_block(self, height: int, expected_count: Optional[int] = None) -> int:
        """
        Remove all index entries for the canonical block at `height`.
        If expected_count is provided and differs from discovered count, we still
        remove what exists and return the observed count.
        Returns the number of removed tx entries.
        """
        removed = 0
        with self.kv.batch() as b:
            i = 0
            while True:
                th = self.kv.get(k_bti(height, i))
                if th is None:
                    break
                # delete reverse entry
                b.delete(k_bti(height, i))
                # delete forward entry
                b.delete(k_txi(th))
                removed += 1
                i += 1
            b.commit()
        return removed

    # --- Debug helpers ---

    def __repr__(self) -> str:
        return "<TxIndex fwd=0x20 rev=0x21>"


__all__ = [
    "TxIndex",
    "TxPointer",
]
