"""
mempool.tx_lookup
=================

A fast in-memory index for:
  • tx_hash  → PoolTx
  • (sender, nonce) → tx_hash
with duplicate-hash and same-(sender,nonce) conflict detection.

This module is intentionally policy-agnostic: it *does not* decide RBF;
it only detects conflicts and provides helpers to atomically replace an
existing (sender, nonce) occupant when the caller has already decided to
allow replacement (e.g., via mempool.priority.should_replace).

Thread-safety: guarded by a re-entrant lock for multi-producer mempools.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

# Optional import to avoid cycles in isolated tests.
try:
    from mempool.types import PoolTx, TxMeta  # type: ignore
except Exception:  # pragma: no cover

    @dataclass
    class TxMeta:  # type: ignore
        sender: str
        nonce: int
        gas_limit: int = 21_000
        size_bytes: int = 100
        first_seen: float = 0.0
        last_seen: float = 0.0
        priority_score: float = 0.0

    @dataclass
    class PoolTx:  # type: ignore
        tx_hash: str
        meta: TxMeta
        fee: object
        raw: bytes


__all__ = ["TxLookupIndex", "AdmissionProbe", "InsertResult"]


@dataclass(frozen=True)
class AdmissionProbe:
    """
    Result of a pre-admission probe.
    - duplicate_hash: same tx_hash already present
    - occupant: an existing transaction occupying (sender, nonce), if any
    """

    duplicate_hash: bool
    occupant: Optional[PoolTx]


@dataclass(frozen=True)
class InsertResult:
    """
    Outcome of an insertion attempt.
    action ∈ {"added", "duplicate_hash", "conflict_nonce", "replaced"}
    """

    ok: bool
    action: str
    replaced_hash: Optional[str] = None


class TxLookupIndex:
    """
    In-memory lookup index with O(1) get-by-hash and O(1) get-by-(sender, nonce).

    Maps:
      - _by_hash: hash -> PoolTx
      - _by_sender_nonce: sender -> { nonce -> hash }

    Public API summary:
      - probe(tx) -> AdmissionProbe
      - insert(tx, *, allow_replace=False) -> InsertResult
      - replace(tx) -> InsertResult (force RBF-style replacement of occupant)
      - remove_by_hash(tx_hash) -> Optional[PoolTx]
      - get(hash) -> Optional[PoolTx]
      - get_by_sender(sender) -> List[PoolTx] (sorted by nonce)
      - get_by_sender_nonce(sender, nonce) -> Optional[PoolTx]
      - pop_by_sender_nonce(sender, nonce) -> Optional[PoolTx]
      - stats() -> dict
      - snapshot() -> dict
    """

    def __init__(self) -> None:
        self._by_hash: Dict[str, PoolTx] = {}
        self._by_sender_nonce: Dict[str, Dict[int, str]] = {}
        self._lock = threading.RLock()

        # Lightweight counters for metrics/diagnostics
        self._ctr_added = 0
        self._ctr_duplicate_hash = 0
        self._ctr_conflict_nonce = 0
        self._ctr_replaced = 0

    # -------------------------------
    # Core helpers
    # -------------------------------

    def probe(self, tx: PoolTx) -> AdmissionProbe:
        """
        Inspect the index for duplicates or nonce occupancy, without mutating state.
        """
        with self._lock:
            dup = tx.tx_hash in self._by_hash
            occ = self._get_occupant_locked(tx.meta.sender, int(tx.meta.nonce))
            return AdmissionProbe(duplicate_hash=dup, occupant=occ)

    def insert(self, tx: PoolTx, *, allow_replace: bool = False) -> InsertResult:
        """
        Insert a transaction. If another tx occupies the same (sender, nonce),
        and allow_replace=False, the insertion is rejected with 'conflict_nonce'.

        If allow_replace=True, the occupant is atomically removed and replaced.
        Duplicate hash is always rejected.

        Returns an InsertResult with the action taken.
        """
        sender = tx.meta.sender
        nonce = int(tx.meta.nonce)

        with self._lock:
            # Duplicate-by-hash?
            if tx.tx_hash in self._by_hash:
                self._ctr_duplicate_hash += 1
                return InsertResult(False, "duplicate_hash", None)

            # Occupant for (sender, nonce)?
            occupant = self._get_occupant_locked(sender, nonce)
            if occupant is not None and not allow_replace:
                self._ctr_conflict_nonce += 1
                return InsertResult(False, "conflict_nonce", occupant.tx_hash)

            # If replacing, clear occupant first
            replaced_hash: Optional[str] = None
            if occupant is not None:
                replaced_hash = occupant.tx_hash
                self._remove_locked(occupant.tx_hash)
                self._ctr_replaced += 1

            # Insert new entries
            self._by_hash[tx.tx_hash] = tx
            bucket = self._by_sender_nonce.get(sender)
            if bucket is None:
                bucket = {}
                self._by_sender_nonce[sender] = bucket
            bucket[nonce] = tx.tx_hash
            self._ctr_added += 1

            return InsertResult(
                True, "replaced" if replaced_hash else "added", replaced_hash
            )

    def replace(self, tx: PoolTx) -> InsertResult:
        """
        Force RBF-style replacement for (sender, nonce). Rejects on duplicate hash.
        """
        return self.insert(tx, allow_replace=True)

    def remove_by_hash(self, tx_hash: str) -> Optional[PoolTx]:
        """
        Remove a transaction by hash and update both indices. Returns the removed tx, if any.
        """
        with self._lock:
            return self._remove_locked(tx_hash)

    # -------------------------------
    # Lookups
    # -------------------------------

    def get(self, tx_hash: str) -> Optional[PoolTx]:
        with self._lock:
            return self._by_hash.get(tx_hash)

    def get_by_sender(self, sender: str) -> List[PoolTx]:
        with self._lock:
            bucket = self._by_sender_nonce.get(sender)
            if not bucket:
                return []
            # Sort by nonce for deterministic order
            items = sorted(bucket.items(), key=lambda kv: kv[0])
            return [self._by_hash[h] for _, h in items if h in self._by_hash]

    def get_by_sender_nonce(self, sender: str, nonce: int) -> Optional[PoolTx]:
        with self._lock:
            bucket = self._by_sender_nonce.get(sender)
            if not bucket:
                return None
            h = bucket.get(int(nonce))
            if not h:
                return None
            return self._by_hash.get(h)

    def pop_by_sender_nonce(self, sender: str, nonce: int) -> Optional[PoolTx]:
        with self._lock:
            tx = self.get_by_sender_nonce(sender, nonce)
            if tx is None:
                return None
            return self._remove_locked(tx.tx_hash)

    # -------------------------------
    # Introspection / metrics
    # -------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_hash)

    def senders(self) -> Iterable[str]:
        with self._lock:
            return list(self._by_sender_nonce.keys())

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "txs": len(self._by_hash),
                "senders": len(self._by_sender_nonce),
                "added": self._ctr_added,
                "duplicate_hash": self._ctr_duplicate_hash,
                "conflict_nonce": self._ctr_conflict_nonce,
                "replaced": self._ctr_replaced,
            }

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            by_sender = {
                s: sorted(nh.items(), key=lambda kv: kv[0])
                for s, nh in self._by_sender_nonce.items()
            }
            return {
                "by_hash_count": len(self._by_hash),
                "by_sender_count": len(self._by_sender_nonce),
                "by_sender": by_sender,
                "stats": self.stats(),
            }

    # -------------------------------
    # Internal primitives (locked)
    # -------------------------------

    def _get_occupant_locked(self, sender: str, nonce: int) -> Optional[PoolTx]:
        bucket = self._by_sender_nonce.get(sender)
        if not bucket:
            return None
        h = bucket.get(int(nonce))
        if not h:
            return None
        return self._by_hash.get(h)

    def _remove_locked(self, tx_hash: str) -> Optional[PoolTx]:
        tx = self._by_hash.pop(tx_hash, None)
        if tx is None:
            return None
        bucket = self._by_sender_nonce.get(tx.meta.sender)
        if bucket is not None:
            bucket.pop(int(tx.meta.nonce), None)
            if not bucket:
                self._by_sender_nonce.pop(tx.meta.sender, None)
        return tx


# ---------------------------------------
# Manual smoke-test when run standalone
# ---------------------------------------
if __name__ == "__main__":  # pragma: no cover

    def mk(sender: str, nonce: int, h: Optional[str] = None) -> PoolTx:
        ts = time.time()
        meta = TxMeta(sender=sender, nonce=nonce, first_seen=ts, last_seen=ts)
        return PoolTx(
            tx_hash=h or f"{sender}:{nonce}:{ts}", meta=meta, fee=object(), raw=b"x"
        )

    idx = TxLookupIndex()

    a5 = mk("A", 5, "hA5")
    a6 = mk("A", 6, "hA6")
    b0 = mk("B", 0, "hB0")
    print("insert A5:", idx.insert(a5))
    print("insert A6:", idx.insert(a6))
    print("insert B0:", idx.insert(b0))
    print("probe A5 (dup):", idx.probe(a5))

    # Nonce conflict without replace
    a5_new = mk("A", 5, "hA5_new")
    print("insert A5_new (no replace):", idx.insert(a5_new, allow_replace=False))
    # Replace occupant
    print("insert A5_new (replace):", idx.insert(a5_new, allow_replace=True))

    print("by sender A:", [t.tx_hash for t in idx.get_by_sender("A")])
    print("get hA6:", idx.get("hA6").tx_hash if idx.get("hA6") else None)
    print("stats:", idx.stats())
    print("snapshot:", idx.snapshot())
