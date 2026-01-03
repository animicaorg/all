"""
mempool.tx_lookup
=================

A fast in-memory index for:
  • tx_hash  → PoolTx
  • sender   → set[tx_hash]

This module is intentionally policy-agnostic: it *does not* decide RBF;
it only detects duplicate hashes and maintains sender groupings for
local replacement/eviction policies.

Thread-safety: guarded by a re-entrant lock for multi-producer mempools.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

# Optional import to avoid cycles in isolated tests.
try:
    from mempool.types import PoolTx, TxMeta  # type: ignore
except Exception:  # pragma: no cover

    @dataclass
    class TxMeta:  # type: ignore
        sender: str
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


__all__ = ["TxLookupIndex", "AdmissionProbe", "InsertResult", "TxIndex", "IndexEntry"]


@dataclass
class IndexEntry:
    """
    Entry stored in the index. Wraps the transaction and its metadata.
    This allows Pool to access both tx and meta easily.
    """

    tx_hash: bytes
    tx: any
    meta: any


@dataclass(frozen=True)
class AdmissionProbe:
    """
    Result of a pre-admission probe.
    - duplicate_hash: same tx_hash already present
    """

    duplicate_hash: bool
    occupant: Optional[PoolTx]


@dataclass(frozen=True)
class InsertResult:
    """
    Outcome of an insertion attempt.
    action ∈ {"added", "duplicate_hash"}
    """

    ok: bool
    action: str
    replaced_hash: Optional[str] = None


class TxLookupIndex:
    """
    In-memory lookup index with O(1) get-by-hash and O(1) per-sender membership.

    Maps:
      - _by_hash: hash -> IndexEntry
      - _by_sender: sender -> set[hash]
    """

    def __init__(self) -> None:
        self._by_hash: Dict[bytes, IndexEntry] = {}
        self._by_sender: Dict[str, Set[bytes]] = {}
        self._lock = threading.RLock()

        # Lightweight counters for metrics/diagnostics
        self._ctr_added = 0
        self._ctr_duplicate_hash = 0

    # -------------------------------
    # Core helpers
    # -------------------------------

    def probe(self, tx: PoolTx) -> AdmissionProbe:
        """
        Inspect the index for duplicates, without mutating state.
        """
        with self._lock:
            dup = tx.tx_hash in self._by_hash
            return AdmissionProbe(duplicate_hash=dup, occupant=None)

    def insert(self, tx: PoolTx, *, allow_replace: bool = False) -> InsertResult:
        """
        Insert a transaction. Duplicate hash is rejected.
        """
        with self._lock:
            if tx.tx_hash in self._by_hash:
                self._ctr_duplicate_hash += 1
                return InsertResult(False, "duplicate_hash", None)

            self._by_hash[tx.tx_hash] = IndexEntry(tx_hash=tx.tx_hash, tx=tx, meta=tx.meta)
            sender = getattr(tx.meta, "sender", "")
            bucket = self._by_sender.setdefault(sender, set())
            bucket.add(tx.tx_hash)
            self._ctr_added += 1
            return InsertResult(True, "added", None)

    def replace(self, tx: PoolTx) -> InsertResult:
        """
        Replace is equivalent to insert in nonce-less mode; duplicate hashes are rejected.
        """
        return self.insert(tx, allow_replace=True)

    def add(self, tx_hash: any, tx: any, meta: any = None) -> None:
        """
        Backward compatibility method for Pool. Stores (tx, meta) as an IndexEntry.
        Accepts tx_hash as bytes or str; normalizes to bytes internally.
        """
        from mempool.types import TxMeta

        # Normalize tx_hash to bytes
        if isinstance(tx_hash, str):
            if tx_hash.startswith("0x"):
                tx_hash = bytes.fromhex(tx_hash[2:])
            else:
                try:
                    tx_hash = bytes.fromhex(tx_hash)
                except ValueError:
                    tx_hash = tx_hash.encode("utf-8")
        elif not isinstance(tx_hash, (bytes, bytearray)):
            tx_hash = bytes(tx_hash)

        if meta is None:
            meta = TxMeta(
                sender=getattr(tx, "sender", ""),
                gas_limit=getattr(tx, "gas_limit", 0),
                size_bytes=getattr(tx, "size_bytes", 0),
                valid_after=getattr(tx, "valid_after", None),
                valid_until=getattr(tx, "valid_until", None),
                salt=getattr(tx, "salt", None),
            )

        with self._lock:
            if tx_hash in self._by_hash:
                from mempool.errors import DuplicateTx

                raise DuplicateTx("transaction already in pool")

            entry = IndexEntry(tx_hash=tx_hash, tx=tx, meta=meta)
            self._by_hash[tx_hash] = entry
            sender = getattr(meta, "sender", "")
            self._by_sender.setdefault(sender, set()).add(tx_hash)

    def get(self, tx_hash: bytes) -> Optional[IndexEntry]:
        with self._lock:
            return self._by_hash.get(tx_hash)

    def remove(self, tx_hash: bytes) -> Optional[IndexEntry]:
        with self._lock:
            entry = self._by_hash.pop(tx_hash, None)
            if entry is None:
                return None
            sender = getattr(entry.meta, "sender", "")
            bucket = self._by_sender.get(sender)
            if bucket is not None:
                bucket.discard(tx_hash)
                if not bucket:
                    self._by_sender.pop(sender, None)
            return entry

    def remove_by_hash(self, tx_hash: bytes) -> Optional[IndexEntry]:
        return self.remove(tx_hash)

    def get_by_sender(self, sender: str) -> List[IndexEntry]:
        with self._lock:
            hashes = sorted(self._by_sender.get(sender, set()))
            return [self._by_hash[h] for h in hashes if h in self._by_hash]

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._by_hash),
                "added": self._ctr_added,
                "duplicate_hash": self._ctr_duplicate_hash,
                "by_sender_count": len(self._by_sender),
            }

    def all_items(self) -> Iterable[tuple[bytes, IndexEntry]]:
        with self._lock:
            return list(self._by_hash.items())

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "size": len(self._by_hash),
                "senders": len(self._by_sender),
                "by_sender": {k: len(v) for k, v in self._by_sender.items()},
            }


TxIndex = TxLookupIndex
