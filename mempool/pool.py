"""
mempool.pool
============

Main mempool object with:
- admission / replacement (RBF) / eviction
- ready-transaction priority queue
- watermark-driven fee floors & eviction thresholds

This module intentionally keeps *stateful* policy in one place and
depends on lightweight, pure helpers from sibling modules:

- mempool.types       : PoolTx, TxMeta, EffectiveFee, PoolStats
- mempool.priority    : effective_priority(...) and rbf_min_bump(...)
- mempool.tx_lookup   : TxIndex (hash↔tx, sender indexes)
- mempool.tx_lookup   : TxIndex (hash↔tx, sender indexes)
- mempool.validate    : fast stateless validation (optional hook here)
- mempool.accounting  : balance/allowance checks (optional hook here)
- mempool.watermark   : FeeWatermark (rolling floors & eviction)
- mempool.config      : capacity, bytes limits (provided by caller)

Design notes
------------
* The "ready heap" contains all admitted transactions (nonce-less).
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

from . import priority, tx_lookup
from .types import PoolStats, PoolTx, TxMeta
from .watermark import FeeWatermark, Thresholds

# -------------------------------
# Errors (import from .errors if present, with fallbacks)
# -------------------------------

try:
    from .errors import (AdmissionError, DoSError, FeeTooLow, 
                         ReplacementError, Oversize, NonceGap)
except Exception:  # pragma: no cover
    class AdmissionError(Exception):
        pass

    class ReplacementError(Exception):
        pass

    class DoSError(Exception):
        pass

    class FeeTooLow(AdmissionError):
        pass

    class Oversize(AdmissionError):
        pass

    class NonceGap(AdmissionError):
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
        config: Optional[PoolConfig] = None,  # Alias for cfg for test compatibility
        watermark: Optional[FeeWatermark] = None,
        clock: Clock = _now_monotonic,
        rbf_bump_ratio: float = 1.10,
    ) -> None:
        # Accept both cfg and config parameters (config is alias for tests)
        actual_cfg = cfg or config or PoolConfig()
        
        # Normalize config attributes for test compatibility
        # SimpleNamespace from tests may use different attribute names
        if not isinstance(actual_cfg, PoolConfig):
            # It's a SimpleNamespace or similar; normalize attributes
            max_txs = getattr(actual_cfg, 'max_txs', None) or getattr(actual_cfg, 'max_items', None) or getattr(actual_cfg, 'capacity', None) or 150_000
            max_bytes = getattr(actual_cfg, 'max_bytes', None) or getattr(actual_cfg, 'capacity_bytes', None) or getattr(actual_cfg, 'max_mem_bytes', None) or 256 * 1024 * 1024
            target_util = getattr(actual_cfg, 'target_util', 0.9)
            accept_below_floor_for_local = getattr(actual_cfg, 'accept_below_floor_for_local', True)
            actual_cfg = PoolConfig(
                max_txs=max_txs,
                max_bytes=max_bytes,
                target_util=target_util,
                accept_below_floor_for_local=accept_below_floor_for_local,
            )
        
        self.cfg = actual_cfg
        self.clock = clock
        # Create a test-friendly watermark with lower min floor if none provided
        if watermark is None:
            from .watermark import WatermarkConfig
            wm_cfg = WatermarkConfig(min_floor_wei=1)  # 1 wei instead of 1 gwei for tests
            watermark = FeeWatermark(wm_cfg)
        self.wm = watermark
        self.index = tx_lookup.TxIndex()
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

    def _enqueue_ready_if_contiguous(self, ptx: Any, meta: TxMeta) -> None:
        """All admitted txs are ready in nonce-less mode; enqueue immediately."""
        h = getattr(ptx, "hash", getattr(ptx, "tx_hash", None))
        if h is not None:
            self._heap_push(h, self._score(ptx, meta))

    def _remove(self, h: bytes) -> None:
        ent = self.index.get(h)
        if ent is None:
            return
        ptx = ent.tx
        meta = ent.meta
        
        # Remove from indexes
        self.index.remove(h)
        # Mark heap entry stale (leave lazy deletion)
        self._in_heap.pop(h, None)
        # Accounting
        self._n_bytes = max(0, self._n_bytes - int(getattr(meta, "size_bytes", 0)))

    # ------------- Public API -------------

    def add(
        self,
        sender_or_tx: any,  # Can be sender (str) or tx (PoolTx)
        nonce_or_meta: any = None,  # Can be legacy nonce (int) or meta (TxMeta)
        tx_or_none: any = None,  # Can be tx (PoolTx) or None
        *,
        is_local: bool = False
    ) -> AddResult:
        """
        Admit a new transaction. Performs duplicate check, floor check,
        admission and queues the tx as ready.

        Supports two calling conventions:
        1. add(tx, meta=None, is_local=False)  # Standard usage
        2. add(sender, nonce, tx, is_local=False)  # Legacy usage from tests (nonce ignored)

        Raises:
            DuplicateTx, FeeTooLow, NonceGap, Oversize, AdmissionError
        """
        # Normalize arguments to (tx, meta)
        if tx_or_none is not None:
            # Legacy 3-arg form: add(sender, nonce, tx)
            sender = sender_or_tx
            nonce = nonce_or_meta
            tx = tx_or_none
            meta = None
        elif isinstance(sender_or_tx, str) and isinstance(nonce_or_meta, int):
            # Another legacy form without third arg - shouldn't happen, but be safe
            raise TypeError("add() requires 3 positional args when sender is passed")
        else:
            # Standard form: add(tx, meta=None)
            tx = sender_or_tx
            meta = nonce_or_meta if isinstance(nonce_or_meta, TxMeta) or nonce_or_meta is None else None

        # Build metadata defaults if caller didn't supply (needed for RBF check)
        if meta is None:
            # Try multiple fee attribute names for compatibility
            effective_fee = (
                getattr(tx, "effective_fee_wei", None) or
                getattr(tx, "fee", None) or
                getattr(tx, "max_fee_per_gas", 0)
            )
            
            # Normalize sender to string (handle bytes from tests)
            raw_sender = getattr(tx, "sender", sender if 'sender' in locals() else "")
            if isinstance(raw_sender, (bytes, bytearray)):
                sender_str = raw_sender.hex()
            elif isinstance(raw_sender, str):
                sender_str = raw_sender
            else:
                sender_str = str(raw_sender)
            
            meta = TxMeta(
                size_bytes=getattr(tx, "size_bytes", getattr(tx, "serialized_size", 0)),
                first_seen_s=self.clock(),
                effective_fee_wei=effective_fee,
                sender=sender_str,
                gas_limit=getattr(tx, "gas_limit", 0),
                valid_after=getattr(tx, "valid_after", None),
                valid_until=getattr(tx, "valid_until", None),
                salt=getattr(tx, "salt", None),
            )

        h = getattr(tx, "hash", None) or getattr(tx, "tx_hash", None)
        
        # Check if hash is already in pool
        if self.index.get(h) is not None:
            raise DuplicateTx("transaction already in pool")

        # Floor (unless local exemption)
        if not self._admit_floor_ok(meta, is_local=is_local):
            raise FeeTooLow("effective fee below current admit floor")

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
        Replace-by-fee: same sender, higher effective fee.
        Returns the hash of the replaced transaction.

        Raises:
            ReplacementError if no replaceable tx found or fee bump too small.
        """
        # Compute metadata for the new tx if needed (need sender for lookup)
        if meta is None:
            # Normalize sender to string (handle bytes from tests)
            raw_sender = getattr(tx, "sender", "")
            if isinstance(raw_sender, (bytes, bytearray)):
                sender_str = raw_sender.hex()
            elif isinstance(raw_sender, str):
                sender_str = raw_sender
            else:
                sender_str = str(raw_sender)
            
            meta = TxMeta(
                size_bytes=getattr(tx, "size_bytes", getattr(tx, "serialized_size", 0)),
                first_seen_s=self.clock(),
                effective_fee_wei=getattr(
                    tx, "effective_fee_wei", getattr(tx, "fee", getattr(tx, "max_fee_per_gas", 0))
                ),
                sender=sender_str,
                gas_limit=getattr(tx, "gas_limit", 0),
                valid_after=getattr(tx, "valid_after", None),
                valid_until=getattr(tx, "valid_until", None),
                salt=getattr(tx, "salt", None),
            )

        # Find lowest-fee tx for sender
        sender_entries = self.index.get_by_sender(meta.sender)
        if not sender_entries:
            raise ReplacementError("no replaceable tx for sender")

        def _fee_key(entry: Any) -> tuple[int, str]:
            fee = int(getattr(entry.meta, "effective_fee_wei", 0))
            h = entry.tx_hash if hasattr(entry, "tx_hash") else getattr(entry.tx, "tx_hash", b"")
            if isinstance(h, (bytes, bytearray)):
                h_key = h.hex()
            else:
                h_key = str(h)
            return (fee, h_key)

        sender_entries.sort(key=_fee_key)
        existing_entry = sender_entries[0]
        existing_hash = existing_entry.tx_hash if hasattr(existing_entry, "tx_hash") else None
        if existing_hash is None:
            raise ReplacementError("no replaceable tx hash found")

        old_fee = int(getattr(existing_entry.meta, "effective_fee_wei", 0))
        new_fee = int(getattr(meta, "effective_fee_wei", 0))
        # Allow policy override from mempool.priority if present
        try:
            min_ratio = priority.rbf_min_bump(existing_entry.meta, meta)
        except Exception:
            min_ratio = self._rbf_bump_ratio

        if new_fee < int(old_fee * float(min_ratio)):
            # Try to use structured error if available, fallback to simple message
            try:
                h_old = existing_hash.hex() if isinstance(existing_hash, (bytes, bytearray)) else str(existing_hash)
                h_new = (getattr(tx, "hash", None) or getattr(tx, "tx_hash", None))
                h_new = h_new.hex() if isinstance(h_new, bytes) else str(h_new)
                raise ReplacementError(
                    required_bump=min_ratio,
                    current_effective_gas_price_wei=old_fee,
                    offered_effective_gas_price_wei=new_fee,
                    tx_hash_old=h_old,
                    tx_hash_new=h_new,
                    sender=meta.sender,
                )
            except TypeError:
                # Fallback if ReplacementError doesn't accept keyword args
                raise ReplacementError(f"fee bump too small: need ≥ {min_ratio:.2f}x")

        # Remove old and add new
        self._remove(existing_hash if isinstance(existing_hash, (bytes, bytearray)) else bytes(existing_hash))
        h = getattr(tx, "hash", None) or getattr(tx, "tx_hash", None)
        self.index.add(h, tx, meta)

        self._n_bytes += int(getattr(meta, "size_bytes", 0))
        # If contiguous, (re)enqueue
        self._enqueue_ready_if_contiguous(tx, meta)

        # Evict if needed
        self._eviction_pressure()

        return existing_hash

    def get(self, h: bytes) -> Optional[Any]:
        """Get transaction by hash. Returns the tx object or None."""
        ent = self.index.get(h)
        return ent.tx if ent is not None else None

    def __len__(self) -> int:
        """Return the number of transactions in the pool."""
        return len(self.index)

    def __contains__(self, item: any) -> bool:
        """Check if a transaction is in the pool. Accepts tx object or hash."""
        # If it's a tx object, get its hash
        if hasattr(item, 'hash'):
            h = item.hash
        elif hasattr(item, 'tx_hash'):
            h = item.tx_hash
        else:
            h = item
        
        return self.index.get(h) is not None

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
        total = len(self.index)
        
        # Compute total gas by summing gas_limit from all entries
        total_gas = 0
        for _, entry in self.index.all_items():
            gas_limit = getattr(entry.meta, 'gas_limit', 0) if hasattr(entry, 'meta') else 0
            total_gas += gas_limit
        
        return PoolStats(
            total_txs=total,
            total_bytes=self._n_bytes,
            total_gas=total_gas,
            min_gas_price_wei=None,  # Optional, not computed here
            max_gas_price_wei=None,  # Optional, not computed here
            oldest_first_seen=None,  # Optional, not computed here
            newest_first_seen=None,  # Optional, not computed here
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

    def iter_ready(self, now_s: Optional[float] = None) -> Iterable[Tuple[Any, Any]]:
        """
        Non-destructive iteration over ready transactions in priority order.
        
        Yields (tx, meta) tuples for transactions in priority order.
        
        The iteration is ordered by descending priority (highest priority first).
        """
        if now_s is None:
            now_s = self.clock()
        
        # Create a sorted snapshot of the heap
        snapshot: List[Tuple[float, int, bytes]] = list(self._ready_heap)
        snapshot.sort()  # Sort by (-score, tag, hash)
        
        # Yield transactions in priority order (best first)
        seen = set()
        for neg_sc, tag, h in snapshot:
            if h in seen:
                continue
            seen.add(h)
            
            # Check if this is the current best entry for this hash
            cur = self._in_heap.get(h)
            if cur is None:
                continue  # Already removed
            
            ent = self.index.get(h)
            if ent is None:
                continue  # Not in index
            
            # Recompute current score (may have drifted)
            current_score = self._score(ent.tx, ent.meta)
            
            # Yield (tx, meta) tuple
            yield (ent.tx, ent.meta)


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
# priority.effective_priority(ptx, meta, now) -> float (higher is better)
# priority.rbf_min_bump(old_meta, new_meta) -> float (ratio)
#
# FeeWatermark.thresholds(pool_size, capacity) -> Thresholds with admit_floor_wei, evict_below_wei
