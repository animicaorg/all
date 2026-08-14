"""Batch construction, headers, and adaptive closure.

A batch is the unit committed to L1. Its header carries the five roots the spec
requires so that a verifier can check the state transition and reconstruct state:

    prev_state_root, new_state_root, transactions_root, receipts_root, data_root
    (+ escrow_root, folded into data_root's domain)

Adaptive closure balances throughput against latency: a batch closes when it hits
a target tx count, byte size, or age — whichever comes first — so we never make
users wait for an enormous batch when a smaller one settles sooner.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional

from . import tx as l2tx
from .constants import (
    DEFAULT_BATCH_MAX_BYTES,
    DEFAULT_BATCH_MAX_MS,
    DEFAULT_BATCH_MAX_TXS,
)
from .executor import ExecResult, Receipt


@dataclass(frozen=True)
class BatchHeader:
    number: int
    l2_chain_id: int
    prev_state_root: bytes
    new_state_root: bytes
    transactions_root: bytes
    receipts_root: bytes
    escrow_root: bytes
    data_root: bytes  # commitment over the DA blob (see l2.da)
    tx_count: int
    timestamp_ms: int  # sequencer wall clock at close; not consensus-critical
    fees_collected: int
    deposited: int
    withdrawn: int

    def encode(self) -> bytes:
        m = bytearray()
        m += b"animica.l2.batchheader.v1"
        m += self.number.to_bytes(8, "big")
        m += self.l2_chain_id.to_bytes(8, "big")
        for root in (
            self.prev_state_root,
            self.new_state_root,
            self.transactions_root,
            self.receipts_root,
            self.escrow_root,
            self.data_root,
        ):
            m += root
        m += self.tx_count.to_bytes(8, "big")
        m += self.fees_collected.to_bytes(16, "big")
        m += self.deposited.to_bytes(16, "big")
        m += self.withdrawn.to_bytes(16, "big")
        # timestamp is NOT hashed into the batch id: two honest sequencers
        # replaying the same txs must agree on the id.
        return bytes(m)

    def batch_id(self) -> bytes:
        return hashlib.sha3_256(self.encode()).digest()

    def to_json(self) -> dict:
        return {
            "number": self.number,
            "l2ChainId": self.l2_chain_id,
            "prevStateRoot": "0x" + self.prev_state_root.hex(),
            "newStateRoot": "0x" + self.new_state_root.hex(),
            "transactionsRoot": "0x" + self.transactions_root.hex(),
            "receiptsRoot": "0x" + self.receipts_root.hex(),
            "escrowRoot": "0x" + self.escrow_root.hex(),
            "dataRoot": "0x" + self.data_root.hex(),
            "txCount": self.tx_count,
            "timestampMs": self.timestamp_ms,
            "feesCollected": str(self.fees_collected),
            "deposited": str(self.deposited),
            "withdrawn": str(self.withdrawn),
            "batchId": "0x" + self.batch_id().hex(),
        }


@dataclass
class Batch:
    header: BatchHeader
    txs: List[l2tx.L2Tx]
    receipts: List[Receipt]
    da_blob: bytes = b""  # populated by l2.da.encode_batch

    def id(self) -> bytes:
        return self.header.batch_id()


def build_header(
    number: int,
    l2_chain_id: int,
    result: ExecResult,
    data_root: bytes,
    tx_count: int,
    timestamp_ms: int,
) -> BatchHeader:
    return BatchHeader(
        number=number,
        l2_chain_id=l2_chain_id,
        prev_state_root=result.prev_state_root,
        new_state_root=result.new_state_root,
        transactions_root=result.transactions_root,
        receipts_root=result.receipts_root,
        escrow_root=result.escrow_root,
        data_root=data_root,
        tx_count=tx_count,
        timestamp_ms=timestamp_ms,
        fees_collected=result.fees_collected,
        deposited=result.deposited,
        withdrawn=result.withdrawn,
    )


@dataclass
class ClosurePolicy:
    """When to close the currently-open batch. All limits are soft targets; the
    sequencer may also close on demand (e.g. an overdue forced tx)."""

    max_txs: int = DEFAULT_BATCH_MAX_TXS
    max_bytes: int = DEFAULT_BATCH_MAX_BYTES
    max_age_ms: int = DEFAULT_BATCH_MAX_MS

    def should_close(self, tx_count: int, byte_size: int, age_ms: int) -> bool:
        return (
            tx_count >= self.max_txs
            or byte_size >= self.max_bytes
            or age_ms >= self.max_age_ms
        )


@dataclass
class BatchBuilder:
    """Accumulates admitted txs and decides when to close. Byte accounting uses
    the encoded tx size so the DA-size limit is respected up front."""

    policy: ClosurePolicy = field(default_factory=ClosurePolicy)
    _txs: List[l2tx.L2Tx] = field(default_factory=list)
    _bytes: int = 0
    _opened_ms: Optional[int] = None

    def reset(self) -> None:
        self._txs = []
        self._bytes = 0
        self._opened_ms = None

    def add(self, t: l2tx.L2Tx, encoded_len: int, now_ms: int) -> None:
        if self._opened_ms is None:
            self._opened_ms = now_ms
        self._txs.append(t)
        self._bytes += encoded_len

    def __len__(self) -> int:
        return len(self._txs)

    @property
    def txs(self) -> List[l2tx.L2Tx]:
        return self._txs

    @property
    def byte_size(self) -> int:
        return self._bytes

    def age_ms(self, now_ms: int) -> int:
        if self._opened_ms is None:
            return 0
        return now_ms - self._opened_ms

    def ready(self, now_ms: int) -> bool:
        if not self._txs:
            return False
        return self.policy.should_close(len(self._txs), self._bytes, self.age_ms(now_ms))
