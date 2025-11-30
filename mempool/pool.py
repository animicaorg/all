"""
mempool.pool
============

Main mempool object with:
- admission / replacement (RBF) / eviction
- per-sender nonce sequencing
- ready-transaction priority queue
- watermark-driven fee floors & eviction thresholds

This module intentionally keeps *stateful* policy in one place and
depends on lightweight, pure helpers from sibling modules:

- mempool.types       : PoolTx, TxMeta, EffectiveFee, PoolStats
- mempool.priority    : effective_priority(...) and rbf_min_bump(...)
- mempool.sequence    : NonceQueues (per-sender ready/held queues)
- mempool.tx_lookup   : TxIndex (hash↔tx, sender indexes)
- mempool.validate    : fast stateless validation (optional hook here)
- mempool.accounting  : balance/allowance checks (optional hook here)
- mempool.watermark   : FeeWatermark (rolling floors & eviction)
- mempool.config      : capacity, bytes limits (provided by caller)

Design notes
------------
* The "ready heap" contains only transactions that are currently
  executable (i.e., nonce-contiguous for their sender).
* If a sender publishes the next nonce, the corresponding txn becomes
  ready and is (re)inserted into the heap lazily.
* Priorities are time-sensitive (age bonus). We implement **lazy
  re-scoring**: when popping, we recompute the current score; if it
  drifted, we push an updated record and skip the stale one.
* Eviction prefers low-fee txs below the watermark's `evict_below_wei`.
  If still above capacity, we evict globally from the absolute worst
  scores across ready+held populations.

All values are deterministic, pure-Python; time-sources are injectable
to keep tests reproducible.
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import priority, sequence, tx_lookup
from .types import PoolStats, PoolTx, TxMeta
from .watermark import FeeWatermark, Thresholds

# -------------------------------
# Errors (re-export from .errors if present)
# -------------------------------


class AdmissionError(Exception):
    pass


class ReplacementError(Exception):
    pass


class DoSError(Exception):
    pass


class FeeTooLow(AdmissionError):
    pass


class DuplicateTx(AdmissionError):
    pass


class NonceGap(AdmissionError):
    pass


class Oversize(AdmissionError):
    pass


# -------------------------------
# Helpers
# -------------------------------

Clock = Callable[[], float]  # returns monotonic seconds


def _now_monotonic() -> float:
    return time.monotonic()


@dataclass
class PoolConfig:
    """
    Minimal configuration the pool needs. The broader mempool.config can
    be mapped into this with a small adapter in your app wiring.

    Attributes:
        max_txs: hard cap on number of txs in pool
        max_bytes: hard cap on summed serialized sizes
        target_util: soft ceiling; above this, apply eviction pressure
        accept_below_floor_for_local: if True, bypass floors for 'local' txs
    """

    max_txs: int = 150_000
    max_bytes: int = 256 * 1024 * 1024
    target_util: float = 0.9
    accept_below_floor_for_local: bool = True


@dataclass
class AddResult:
    new: bool
    replaced_hash: Optional[bytes] = None


# -------------------------------
# Pool
# -------------------------------


class Pool:
    """
    The mempool.

    Public API:
        add(tx: PoolTx, meta: Optional[TxMeta]=None, *, is_local=False) -> AddResult
        replace(tx: PoolTx, meta: Optional[TxMeta]=None) -> bytes  # old hash
        get(hash: bytes) -> Optional[PoolTx]
        fetch_ready(max_txs: int, max_bytes: int) -> List[PoolTx]
        remove_included(hashes: Iterable[bytes]) -> None
        on_new_block(inclusion_fees_wei: Iterable[int]) -> None
        thresholds() -> Thresholds
        stats() -> PoolStats
    """

    __slots__ = (
        "cfg",
        "clock",
        "wm",
        "index",
        "seqs",
        "_n_bytes",
        "_ready_heap",
        "_heap_tag",
        "_in_heap",
        "_rbf_bump_ratio",
    )

    def __init__(
        self,
        cfg: Optional[PoolConfig] = None,
        *,
        watermark: Optional[FeeWatermark] = None,
        clock: Clock = _now_monotonic,
        rbf_bump_ratio: float = 1.10,
    ) -> None:
        self.cfg = cfg or PoolConfig()
        self.clock = clock
        self.wm = watermark or FeeWatermark()
        self.index = tx_lookup.TxIndex()
        self.seqs = sequence.NonceQueues()
        self._n_bytes: int = 0

        # Ready PQ: items are (-score, tag, tx_hash)
        # `tag` is a monotonically increasing int to break ties and
        # defeat ABA when lazy re-scoring.
        self._ready_heap: List[Tuple[float, int, bytes]] = []
        self._heap_tag: int = 0
        self._in_heap: Dict[bytes, Tuple[float, int]] = {}  # hash -> (-score, tag)

        # RBF bump ratio (e.g., 1.10 = +10% required)
        self._rbf_bump_ratio = float(rbf_bump_ratio)

    # ------------- Private utilities -------------

    def _score(self, ptx: PoolTx, meta: TxMeta) -> float:
        """
        Compute a *higher is better* score for ready ordering.
        We defer logic to mempool.priority if present, adding an age bonus.
        """
        now = self.clock()
        try:
            base = priority.effective_priority(ptx, meta, now=now)
        except Exception:
            # Conservative fallback: fee_per_gas / (1 + size_kb)
            fee = getattr(meta, "effective_fee_wei", None)
            if fee is None:
                fee = getattr(ptx, "effective_fee_wei", 0)
            size = getattr(meta, "size_bytes", 1)
            base = float(int(fee)) / (1.0 + (size / 1024.0))

        # Add small age bonus to encourage FIFO among near-equals
        age_s = max(0.0, now - getattr(meta, "first_seen_s", now))
        return float(base) * (1.0 + min(0.10, age_s / 120.0))  # +10% cap at 2 minutes

    def _heap_push(self, tx_hash: bytes, score: float) -> None:
        self._heap_tag += 1
        item = (-float(score), self._heap_tag, tx_hash)
        self._in_heap[tx_hash] = (item[0], item[1])
        heapq.heappush(self._ready_heap, item)

    def _heap_rescore_if_needed(self, ptx: PoolTx, meta: TxMeta) -> None:
        h = ptx.hash
        new_score = self._score(ptx, meta)
        prev = self._in_heap.get(h)
        if prev is None or prev[0] != -new_score:
            self._heap_push(h, new_score)

    def _pop_valid_ready(self) -> Optional[bytes]:
        """Pop the best *currently-valid* ready tx hash, skipping stale heap entries."""
        while self._ready_heap:
            neg_sc, tag, h = heapq.heappop(self._ready_heap)
            cur = self._in_heap.get(h)
            if cur is None:
                continue  # was removed
            if cur[0] == neg_sc and cur[1] == tag:
                # this is the latest scoring for h
                self._in_heap.pop(h, None)
                return h
            # else: stale entry; skip
        return None

    def _admit_floor_ok(self, meta: TxMeta, is_local: bool) -> bool:
        th = self.wm.thresholds(pool_size=len(self.index), capacity=self.cfg.max_txs)
        if is_local and self.cfg.accept_below_floor_for_local:
            return True
        eff = getattr(meta, "effective_fee_wei", None)
        if eff is None:
            # Try to look at tx object
            eff = getattr(meta, "fee_per_gas_wei", 0)
        return int(eff) >= int(th.admit_floor_wei)

    def _eviction_pressure(self) -> None:
        """Evict under watermark & hard caps."""
        # Watermark-based pass: evict those falling below 'evict_below_wei'
        th = self.wm.thresholds(pool_size=len(self.index), capacity=self.cfg.max_txs)
        if th.evict_below_wei > 0:
            victims: List[bytes] = []
            # Scan a limited sample: worst N in sender-held + ready sets.
            # Strategy: look at ready heap *approximate* tail by re-scoring a small sample.
            sample: List[Tuple[int, bytes]] = []  # (fee_wei, hash)
            for h, entry in list(self.index.all_items())[:2048]:
                meta = entry.meta
                eff = getattr(meta, "effective_fee_wei", 0)
                sample.append((int(eff), h))
            sample.sort(key=lambda x: x[0])
            for fee, h in sample:
                if fee >= th.evict_below_wei:
                    break
                victims.append(h)
            for h in victims[:1024]:
                self._remove(h)

        # Hard caps: drop worst scores until within (txs, bytes)
        def over_caps() -> bool:
            return (len(self.index) > self.cfg.max_txs) or (
                self._n_bytes > self.cfg.max_bytes
            )

        if over_caps():
            # Build a poor-man's "worst first" list without materializing all scores.
            scored: List[Tuple[float, bytes]] = []
            for h, entry in self.index.all_items():
                try:
                    s = self._score(entry.tx, entry.meta)
                except Exception:
                    s = 0.0
                scored.append((float(s), h))
            scored.sort(key=lambda t: t[0])  # ascending: worst first
            i = 0
            while over_caps() and i < len(scored):
                self._remove(scored[i][1])
                i += 1

    def _enqueue_ready_if_contiguous(self, ptx: PoolTx, meta: TxMeta) -> None:
        """If the tx has the sender's next nonce, mark ready and enqueue."""
        if self.seqs.is_ready(ptx.sender, ptx.nonce):
            self._heap_push(ptx.hash, self._score(ptx, meta))

    def _remove(self, h: bytes) -> None:
        ent = self.index.get(h)
        if ent is None:
            return
        ptx = ent.tx
        meta = ent.meta
        # Remove from indexes
        self.index.remove(h)
        self.seqs.remove(ptx.sender, ptx.nonce, h)
        # Mark heap entry stale (leave lazy deletion)
        self._in_heap.pop(h, None)
        # Accounting
        self._n_bytes = max(0, self._n_bytes - int(getattr(meta, "size_bytes", 0)))
        # Promote any newly contiguous txs for this sender
        nxt = self.seqs.promote_next_ready(ptx.sender)
        if nxt:
            # We may have many; enqueue all that became ready
            for hh in nxt:
                ent2 = self.index.get(hh)
                if ent2:
                    self._heap_push(hh, self._score(ent2.tx, ent2.meta))

    # ------------- Public API -------------

    def add(
        self, tx: PoolTx, meta: Optional[TxMeta] = None, *, is_local: bool = False
    ) -> AddResult:
        """
        Admit a new transaction. Performs duplicate check, floor check,
        nonce sequencing and queues the tx as ready if contiguous.

        Raises:
            DuplicateTx, FeeTooLow, NonceGap, Oversize, AdmissionError
        """
        h = tx.hash
        if self.index.get(h) is not None:
            raise DuplicateTx("transaction already in pool")

        # Build metadata defaults if caller didn't supply
        if meta is None:
            meta = TxMeta(
                size_bytes=getattr(tx, "size_bytes", getattr(tx, "serialized_size", 0)),
                first_seen_s=self.clock(),
                effective_fee_wei=getattr(
                    tx, "effective_fee_wei", getattr(tx, "max_fee_per_gas", 0)
                ),
                sender=tx.sender,
                nonce=tx.nonce,
            )

        # Floor (unless local exemption)
        if not self._admit_floor_ok(meta, is_local=is_local):
            raise FeeTooLow("effective fee below current admit floor")

        # Insert into per-sender sequence (may be held if gap)
        gap = self.seqs.add(tx.sender, tx.nonce, h)
        if gap:
            # We allowed adding, but it's not ready yet; still keep it.
            # Check oversize now that it is tracked.
            pass

        # Index & accounting
        self.index.add(h, tx, meta)
        self._n_bytes += int(getattr(meta, "size_bytes", 0))

        # If contiguous -> ready heap
        self._enqueue_ready_if_contiguous(tx, meta)

        # Eviction if we are over soft or hard limits
        self._eviction_pressure()

        return AddResult(new=True, replaced_hash=None)

    def replace(self, tx: PoolTx, meta: Optional[TxMeta] = None) -> bytes:
        """
        Replace-by-fee: same sender & nonce, higher effective fee.
        Returns the hash of the replaced transaction.

        Raises:
            ReplacementError if no replaceable tx found or fee bump too small.
        """
        # Find if a tx exists for (sender, nonce)
        existing_hash = self.seqs.get_hash(tx.sender, tx.nonce)
        if existing_hash is None:
            raise ReplacementError("no replaceable tx for sender/nonce")

        existing = self.index.get(existing_hash)
        if existing is None:
            raise ReplacementError("inconsistent indices")

        # Compute metadata for the new tx if needed
        if meta is None:
            meta = TxMeta(
                size_bytes=getattr(tx, "size_bytes", getattr(tx, "serialized_size", 0)),
                first_seen_s=self.clock(),
                effective_fee_wei=getattr(
                    tx, "effective_fee_wei", getattr(tx, "max_fee_per_gas", 0)
                ),
                sender=tx.sender,
                nonce=tx.nonce,
            )

        old_fee = int(getattr(existing.meta, "effective_fee_wei", 0))
        new_fee = int(getattr(meta, "effective_fee_wei", 0))
        # Allow policy override from mempool.priority if present
        try:
            min_ratio = priority.rbf_min_bump(existing.meta, meta)
        except Exception:
            min_ratio = self._rbf_bump_ratio

        if new_fee < int(old_fee * float(min_ratio)):
            raise ReplacementError(f"fee bump too small: need ≥ {min_ratio:.2f}x")

        # Remove old and add new (preserve nonce sequencing position)
        self._remove(existing_hash)
        self.index.add(tx.hash, tx, meta)
        self.seqs.add(tx.sender, tx.nonce, tx.hash)

        self._n_bytes += int(getattr(meta, "size_bytes", 0))
        # If contiguous, (re)enqueue
        self._enqueue_ready_if_contiguous(tx, meta)

        # Evict if needed
        self._eviction_pressure()

        return existing_hash

    def get(self, h: bytes) -> Optional[PoolTx]:
        ent = self.index.get(h)
        return ent.tx if ent is not None else None

    def fetch_ready(self, max_txs: int, max_bytes: int) -> List[PoolTx]:
        """
        Pop up to (max_txs, max_bytes) of *currently ready* transactions,
        re-scoring on the fly. Returned txs are **removed from the pool**.

        The caller (block builder) is responsible for final chain checks;
        if some tx cannot be included, it can be re-admitted.
        """
        out: List[PoolTx] = []
        n_bytes = 0

        while len(out) < max_txs and (n_bytes < max_bytes):
            h = self._pop_valid_ready()
            if h is None:
                break
            ent = self.index.get(h)
            if ent is None:
                continue  # race with removal
            # Re-validate readiness (sender nonce still contiguous)
            if not self.seqs.is_ready(ent.tx.sender, ent.tx.nonce):
                # Not ready anymore (reorg in local accounting?); skip
                continue
            size = int(getattr(ent.meta, "size_bytes", 0))
            if out and (n_bytes + size) > max_bytes:
                # Put it back into heap for later
                self._heap_rescore_if_needed(ent.tx, ent.meta)
                break

            out.append(ent.tx)
            n_bytes += size
            # Remove from pool (and promote next for sender)
            self._remove(h)

        return out

    def remove_included(self, hashes: Iterable[bytes]) -> None:
        """Drop transactions that were included on-chain."""
        for h in hashes:
            self._remove(h)

    def on_new_block(self, inclusion_fees_wei: Iterable[int]) -> None:
        """
        Feed recent inclusion prices to the watermark (adjust floors) and
        apply an eviction pass if utilization remains high.
        """
        self.wm.observe_block_inclusions(inclusion_fees_wei)
        # Run a quick pressure pass if above target util
        util = len(self.index) / max(1, self.cfg.max_txs)
        if util >= self.cfg.target_util:
            self._eviction_pressure()

    def thresholds(self) -> Thresholds:
        return self.wm.thresholds(pool_size=len(self.index), capacity=self.cfg.max_txs)

    # ------------- Introspection -------------

    def stats(self) -> PoolStats:
        """Return a snapshot of pool stats. (Fields defined in mempool.types)"""
        ready = len(self._in_heap)
        total = len(self.index)
        held = max(0, total - ready)
        th = self.thresholds()
        return PoolStats(
            total=total,
            ready=ready,
            held=held,
            bytes=self._n_bytes,
            admit_floor_wei=th.admit_floor_wei,
            evict_below_wei=th.evict_below_wei,
            utilization=(
                float(total) / float(self.cfg.max_txs) if self.cfg.max_txs else 0.0
            ),
        )

    # ------------- Iteration helpers (optional) -------------

    def iter_ready_peek(self, limit: int = 64) -> List[Tuple[bytes, float]]:
        """
        Non-destructive peek at the top-N ready txs as (hash, current_score).
        Intended for debugging/telemetry only.
        """
        snapshot: List[Tuple[float, int, bytes]] = list(self._ready_heap)
        snapshot.sort()
        out: List[Tuple[bytes, float]] = []
        for neg_sc, tag, h in snapshot[:limit]:
            ent = self.index.get(h)
            if not ent:
                continue
            sc = self._score(ent.tx, ent.meta)
            out.append((h, sc))
        return out


# -------------------------------
# Minimal TxIndex compatibility shims
# -------------------------------

# The pool expects TxIndex to provide:
# - __len__()
# - get(hash) -> Entry|None
# - add(hash, tx, meta) -> None
# - remove(hash) -> None
# - all_items() -> Iterable[Tuple[bytes, Entry]]
#
# And Entry has fields: tx, meta.
#
# NonceQueues must provide:
# - add(sender, nonce, hash) -> bool gap   (True if held due to gap)
# - remove(sender, nonce, hash)
# - is_ready(sender, nonce) -> bool
# - promote_next_ready(sender) -> Optional[List[bytes]]
# - get_hash(sender, nonce) -> Optional[bytes]
#
# priority.effective_priority(ptx, meta, now) -> float (higher is better)
# priority.rbf_min_bump(old_meta, new_meta) -> float (ratio)
#
# FeeWatermark.thresholds(pool_size, capacity) -> Thresholds with admit_floor_wei, evict_below_wei
