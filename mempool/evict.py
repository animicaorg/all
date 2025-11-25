"""
mempool.evict
=============

Eviction planner for the mempool. This module is deliberately decoupled
from any concrete Pool implementation: callers pass a *snapshot* of
candidate entries and current utilization, and the planner returns a list
of victims to evict (ordered) along with reasons.

Design goals
------------
- Global low-priority eviction: when utilization crosses a high-water
  mark, remove the lowest-priority transactions until we are back below
  a low-water mark. Priority is delegated to mempool.priority if
  available, otherwise a sensible fallback (fee/byte with a tiny age
  boost) is used.
- Per-sender fairness caps: enforce a maximum number of concurrent
  transactions per sender by evicting that sender's lowest-priority
  transactions first.
- Memory pressure handler: optionally react to host memory signals by
  evicting more aggressively (dropping protections and targeting a lower
  watermark).

Inputs are duck-typed; we avoid importing heavy modules or concrete pool
types. This module is purely functional/policy and performs no I/O.

Expected meta/tx attributes
---------------------------
Each candidate entry should expose at least:
  - tx_hash: bytes-like (unique id; hex ok)
  - sender:  bytes-like (address id for fairness)
  - size_bytes: int (serialized size)
  - first_seen_s: float (monotonic seconds) [optional]
  - local: bool (submitted by local/trusted source) [optional]
  - is_replacement_candidate: bool (protect while pending replace) [optional]
  - effective_fee_wei: int (for fallback priority) [in meta OR tx]

If mempool.priority is available it may supply:
  - effective_priority(tx, meta, now_s) -> float
  - rbf_min_bump(old_meta, new_meta) -> float  (used elsewhere)

The EvictionPlanner will call effective_priority if present; otherwise
it uses a fallback: (effective_fee_wei / size_bytes) * (1 + min(age/600, 0.10)).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Dict, Tuple, Optional, Callable, Any
import math
import time
import heapq

# Optional priority integration
try:
    from . import priority as _priority
except Exception:  # pragma: no cover
    _priority = None  # type: ignore

Clock = Callable[[], float]


def _now_monotonic() -> float:
    return time.monotonic()


# -------------------------
# Config
# -------------------------

@dataclass
class EvictionConfig:
    """
    Tuning knobs for eviction and fairness.

    Watermarks:
      - If high/low water are None, they are derived from capacity_bytes
        as high = 0.95 * capacity, low = 0.90 * capacity.

    Fairness:
      - max_per_sender: upper bound on outstanding txs per sender.
        Set 0/None to disable.
      - min_keep_per_sender: protect this many best-priority txs per sender.

    Protections:
      - protect_local: do not evict 'local' txs in normal mode.
      - protect_newer_than_s: avoid evicting very fresh txs.
      - protect_replacement_candidates: avoid evicting txs flagged as pending RBF targets.

    Memory pressure:
      - emergency_when_rss_over_bytes: if provided and rss_bytes >= threshold,
        we enter emergency mode: protections are relaxed and we target a deeper
        drop below the low watermark.
      - emergency_low_factor: multiply low_water_bytes by this factor (e.g. 0.80)
        as the emergency target.
    """
    high_water_bytes: Optional[int] = None
    low_water_bytes: Optional[int] = None

    max_per_sender: Optional[int] = 128
    min_keep_per_sender: int = 1

    protect_local: bool = True
    protect_newer_than_s: float = 5.0
    protect_replacement_candidates: bool = True

    emergency_when_rss_over_bytes: Optional[int] = None
    emergency_low_factor: float = 0.80


# -------------------------
# Snapshot & Candidate
# -------------------------

@dataclass(order=True)
class EvictCandidate:
    """
    Comparable by (priority asc, size desc) via `sort_key`.

    We keep the original tx/meta for callers who want to double-check or
    introspect before performing eviction.
    """
    # The first two fields are used in sorting (priority asc, then size desc)
    sort_priority: float
    sort_size: int
    # Non-sort fields
    tx_hash: bytes = field(compare=False)
    sender: bytes = field(compare=False)
    size_bytes: int = field(compare=False)
    age_s: float = field(compare=False)
    local: bool = field(compare=False, default=False)
    is_replacement_candidate: bool = field(compare=False, default=False)
    tx: Any = field(compare=False, default=None)
    meta: Any = field(compare=False, default=None)
    reason_hint: Optional[str] = field(compare=False, default=None)

    @staticmethod
    def _safe_bytes(v: Any) -> bytes:
        if v is None:
            return b""
        if isinstance(v, bytes):
            return v
        if isinstance(v, str):
            try:
                # Accept hex-like strings; otherwise encode utf-8
                if all(c in "0123456789abcdefABCDEF" for c in v.strip("0x")):
                    return bytes.fromhex(v.strip().removeprefix("0x"))
            except Exception:
                pass
            return v.encode("utf-8", "ignore")
        return bytes(v)

    @classmethod
    def from_entry(cls, tx: Any, meta: Any, *, now_s: float, priority_val: float) -> "EvictCandidate":
        tx_hash = getattr(meta, "tx_hash", getattr(tx, "tx_hash", getattr(tx, "hash", None)))
        sender = getattr(tx, "sender", getattr(meta, "sender", None))
        size = int(getattr(meta, "size_bytes", getattr(tx, "size_bytes", 0)) or 0)
        first_seen = float(getattr(meta, "first_seen_s", getattr(tx, "first_seen_s", now_s)) or now_s)
        local = bool(getattr(meta, "local", getattr(tx, "local", False)))
        is_repl = bool(getattr(meta, "is_replacement_candidate",
                               getattr(tx, "is_replacement_candidate", False)))
        age = max(0.0, now_s - first_seen)

        # `sort_size` is negative to prefer evicting larger items (free bytes faster)
        return cls(
            sort_priority=float(priority_val),
            sort_size=-size,
            tx_hash=cls._safe_bytes(tx_hash),
            sender=cls._safe_bytes(sender),
            size_bytes=size,
            age_s=age,
            local=local,
            is_replacement_candidate=is_repl,
            tx=tx,
            meta=meta,
        )


@dataclass
class PoolSnapshot:
    """
    A light snapshot of pool content and utilization for planning.

    Attributes:
      - entries: iterable of (tx, meta) pairs
      - bytes_used: current total bytes used by the pool
      - capacity_bytes: configured capacity of the pool
      - rss_bytes: optional current process RSS to assess memory pressure
    """
    entries: Iterable[Tuple[Any, Any]]
    bytes_used: int
    capacity_bytes: int
    rss_bytes: Optional[int] = None


@dataclass
class Victim:
    tx_hash: bytes
    reason: str  # "sender_cap" | "global_low_priority" | "emergency_pressure"
    size_bytes: int
    priority: float
    sender: bytes


# -------------------------
# Priority helpers
# -------------------------

def _fallback_effective_priority(tx: Any, meta: Any, now_s: float) -> float:
    """
    Simple, stable priority if no mempool.priority module is available.

    priority ≈ (effective_fee_wei / size_bytes) * (1 + age_boost)
    where age_boost ≤ 10% grows linearly over 10 minutes.
    """
    eff_fee = getattr(meta, "effective_fee_wei", getattr(tx, "effective_fee_wei", 0)) or 0
    size = int(getattr(meta, "size_bytes", getattr(tx, "size_bytes", 1)) or 1)
    first_seen = float(getattr(meta, "first_seen_s", getattr(tx, "first_seen_s", now_s)) or now_s)
    age_s = max(0.0, now_s - first_seen)
    age_boost = min(age_s / 600.0, 0.10)
    return (int(eff_fee) / max(1, size)) * (1.0 + age_boost)


def _effective_priority(tx: Any, meta: Any, now_s: float) -> float:
    if _priority is not None and hasattr(_priority, "effective_priority"):
        try:
            return float(_priority.effective_priority(tx, meta, now_s))  # type: ignore[attr-defined]
        except Exception:
            pass
    return _fallback_effective_priority(tx, meta, now_s)


# -------------------------
# Planner
# -------------------------

class EvictionPlanner:
    def __init__(self, cfg: Optional[EvictionConfig] = None, *, clock: Clock = _now_monotonic):
        self.cfg = cfg or EvictionConfig()
        self._clock = clock

    # --- internal watermarks ---

    def _wm(self, capacity_bytes: int) -> Tuple[int, int]:
        hi = self.cfg.high_water_bytes
        lo = self.cfg.low_water_bytes
        if hi is None:
            hi = int(capacity_bytes * 0.95)
        if lo is None:
            lo = int(capacity_bytes * 0.90)
        lo = min(lo, hi)  # never above high water
        return hi, lo

    def _is_emergency(self, rss_bytes: Optional[int]) -> bool:
        thr = self.cfg.emergency_when_rss_over_bytes
        return bool(thr is not None and rss_bytes is not None and rss_bytes >= int(thr))

    # --- candidate building ---

    def _build_candidates(self, snapshot: PoolSnapshot) -> List[EvictCandidate]:
        now = self._clock()
        cands: List[EvictCandidate] = []
        for tx, meta in snapshot.entries:
            pr = _effective_priority(tx, meta, now)
            cands.append(EvictCandidate.from_entry(tx, meta, now_s=now, priority_val=pr))
        return cands

    # --- fairness ---

    def _enforce_sender_caps(self, cands: List[EvictCandidate]) -> List[Victim]:
        max_per = self.cfg.max_per_sender
        if not max_per or max_per <= 0:
            return []

        # Group by sender, sorted by priority descending (best first)
        by_sender: Dict[bytes, List[EvictCandidate]] = {}
        for c in cands:
            by_sender.setdefault(c.sender, []).append(c)
        victims: List[Victim] = []

        for sender, lst in by_sender.items():
            lst.sort(key=lambda x: (-x.sort_priority, x.size_bytes))  # best first
            cap = int(max_per)
            keep = min(self.cfg.min_keep_per_sender, cap)
            # If within cap, nothing to do
            if len(lst) <= cap:
                continue
            # Evict overflow: start from worst, but leave at least 'keep'
            overflow = len(lst) - cap
            worst = sorted(lst[keep:], key=lambda x: (x.sort_priority, -x.size_bytes))
            for c in worst[:overflow]:
                victims.append(Victim(
                    tx_hash=c.tx_hash,
                    reason="sender_cap",
                    size_bytes=c.size_bytes,
                    priority=float(c.sort_priority),
                    sender=c.sender,
                ))
        return victims

    # --- global low-priority ---

    def _global_evictions(
        self,
        cands: List[EvictCandidate],
        *,
        bytes_used: int,
        capacity_bytes: int,
        emergency: bool,
    ) -> List[Victim]:
        hi, lo = self._wm(capacity_bytes)
        if not emergency and bytes_used <= hi:
            return []

        # Determine target
        target = lo
        reason = "global_low_priority"
        if emergency:
            target = max(0, int(lo * float(self.cfg.emergency_low_factor)))
            reason = "emergency_pressure"

        bytes_to_free = max(0, bytes_used - target)
        if bytes_to_free <= 0:
            return []

        # Build the eviction heap of *eligible* candidates
        heap: List[EvictCandidate] = []
        for c in cands:
            if not emergency:
                if self.cfg.protect_local and c.local:
                    continue
                if self.cfg.protect_replacement_candidates and c.is_replacement_candidate:
                    continue
                if c.age_s < self.cfg.protect_newer_than_s:
                    continue
            # In emergency mode, no protections apply
            heap.append(c)

        # Sort by (priority asc, size desc): evict the worst/cheapest-to-keep first,
        # and among equals prefer larger ones to free bytes quicker.
        heap.sort(key=lambda x: (x.sort_priority, -x.size_bytes))

        victims: List[Victim] = []
        freed = 0
        for c in heap:
            if freed >= bytes_to_free:
                break
            victims.append(Victim(
                tx_hash=c.tx_hash,
                reason=reason,
                size_bytes=c.size_bytes,
                priority=float(c.sort_priority),
                sender=c.sender,
            ))
            freed += c.size_bytes
        return victims

    # --- public API ---

    def plan(self, snapshot: PoolSnapshot) -> List[Victim]:
        """
        Compute an ordered list of victims to evict.

        Algorithm:
          1) Build candidates with priorities.
          2) Apply per-sender fairness caps (evict worst overflow per sender).
          3) Recompute utilization after fairness evictions and, if still
             above high-water (or in emergency), evict globally lowest
             priority transactions until we reach the low-water target.
        """
        cands = self._build_candidates(snapshot)

        # Per-sender fairness first
        fairness_victims = self._enforce_sender_caps(cands)
        if not fairness_victims:
            new_bytes_used = snapshot.bytes_used
        else:
            # Remove fairness victims from the candidate pool for the next stage
            evicted_hashes = {v.tx_hash for v in fairness_victims}
            cands = [c for c in cands if c.tx_hash not in evicted_hashes]
            freed = sum(v.size_bytes for v in fairness_victims)
            new_bytes_used = max(0, snapshot.bytes_used - freed)

        # Global low-priority eviction
        emergency = self._is_emergency(snapshot.rss_bytes)
        global_victims = self._global_evictions(
            cands,
            bytes_used=new_bytes_used,
            capacity_bytes=snapshot.capacity_bytes,
            emergency=emergency,
        )

        # Merge plans; fairness victims first (stable order)
        plan = fairness_victims + global_victims
        return plan


# -------------------------
# Convenience top-level
# -------------------------

def plan_evictions(
    *,
    entries: Iterable[Tuple[Any, Any]],
    bytes_used: int,
    capacity_bytes: int,
    rss_bytes: Optional[int] = None,
    cfg: Optional[EvictionConfig] = None,
    clock: Clock = _now_monotonic,
) -> List[Victim]:
    """
    One-shot helper that constructs a snapshot and runs the planner.
    """
    planner = EvictionPlanner(cfg=cfg, clock=clock)
    snapshot = PoolSnapshot(
        entries=entries,
        bytes_used=int(bytes_used),
        capacity_bytes=int(capacity_bytes),
        rss_bytes=rss_bytes,
    )
    return planner.plan(snapshot)


# -------------------------
# Debug / pretty printing
# -------------------------

def format_plan(plan: List[Victim]) -> str:
    """Human-friendly single-line summary per victim."""
    out = []
    for v in plan:
        h = v.tx_hash.hex() if isinstance(v.tx_hash, (bytes, bytearray)) else str(v.tx_hash)
        s = v.sender.hex() if isinstance(v.sender, (bytes, bytearray)) else str(v.sender)
        out.append(f"{h[:12]}.. sender={s[:10]}.. size={v.size_bytes}B prio={v.priority:.6g} reason={v.reason}")
    return "\n".join(out)


__all__ = [
    "EvictionConfig",
    "EvictionPlanner",
    "PoolSnapshot",
    "EvictCandidate",
    "Victim",
    "plan_evictions",
    "format_plan",
]
