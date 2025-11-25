"""
mempool.watermark
=================

Rolling *min-fee* watermark and *eviction* thresholds for the mempool.

This module tracks recent fee observations (both from admitted txs and
recently mined blocks) and computes two key thresholds:

- `admit_floor_wei`: the minimum effective fee-per-gas a new tx should pay
  to be admitted without backpressure.
- `evict_below_wei`: when the pool is over target utilization, txs with
  effective fee below this threshold are first in line for eviction.

Design goals
------------
- Fast, allocation-free hot path (pure Python, no I/O).
- Log-space histogram with exponential decay to approximate percentiles.
- EMA-smoothed floor fed by *block inclusion* prices to resist spam games.
- Hysteresis (low/high watermarks) to avoid oscillation.
- Deterministic with injectable time for tests.

Conventions
-----------
- All fees are *effective* fee-per-gas, expressed in **wei** as Python `int`.
- Pool utilization = `current_size / capacity` (floats).
- Call `observe_admission(fee)` when a tx is accepted to the pool.
- Call `observe_block_inclusions(fees)` after each new block.
- Call `thresholds(pool_size, capacity)` to retrieve current floors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple, List
import math
import time


# -------------------------------
# Utilities
# -------------------------------

def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

def _safe_int(x: float) -> int:
    if math.isinf(x) or math.isnan(x):
        return 0
    return int(x) if x >= 0 else 0


# -------------------------------
# Log-space histogram
# -------------------------------

class LogHistogram:
    """
    Approximate order statistics over a wide fee range using logarithmic bins.

    Bins are spaced uniformly in log10-space between [min_wei, max_wei].
    A decay factor in (0,1] exponentially downweights older observations.

    This structure is memory-constant and O(B) per percentile query.
    """

    __slots__ = ("_edges", "_counts", "_sum", "_decay")

    def __init__(self, *, min_wei: int = 1, max_wei: int = 10**12, bins: int = 96, decay: float = 0.95) -> None:
        assert min_wei >= 1 and max_wei > min_wei and bins >= 8
        self._edges: List[float] = []
        log_min = math.log10(float(min_wei))
        log_max = math.log10(float(max_wei))
        step = (log_max - log_min) / bins
        # edges has length bins+1
        for i in range(bins + 1):
            self._edges.append(10.0 ** (log_min + step * i))
        self._counts: List[float] = [0.0] * bins
        self._sum: float = 0.0
        self._decay = float(decay)

    @property
    def bins(self) -> int:
        return len(self._counts)

    def decay(self) -> None:
        """Apply exponential decay to all buckets (on block boundary, for example)."""
        if self._decay >= 1.0:
            return
        d = self._decay
        self._counts = [c * d for c in self._counts]
        self._sum *= d

    def observe(self, fee_wei: int, weight: float = 1.0) -> None:
        """Add one observation into its log-space bucket."""
        if fee_wei <= 0 or weight <= 0.0:
            return
        # Binary search for bucket
        lo, hi = 0, len(self._counts)
        x = float(fee_wei)
        edges = self._edges
        while lo < hi:
            mid = (lo + hi) // 2
            if x >= edges[mid + 1]:
                lo = mid + 1
            elif x < edges[mid]:
                hi = mid
            else:
                lo = mid
                break
        idx = _clamp(lo, 0, len(self._counts) - 1)
        self._counts[int(idx)] += weight
        self._sum += weight

    def percentile(self, q: float) -> int:
        """
        Approximate the q-percentile (q in [0,1]) of the observed distribution.
        Returns 0 if there is insufficient data.
        """
        if self._sum <= 0.0:
            return 0
        q = _clamp(q, 0.0, 1.0)
        target = self._sum * q
        acc = 0.0
        counts = self._counts
        edges = self._edges
        for i, c in enumerate(counts):
            nxt = acc + c
            if nxt >= target:
                # Linear interpolate within the bucket by fraction
                frac = 0.0 if c <= 1e-12 else (target - acc) / c
                lo = edges[i]
                hi = edges[i + 1]
                val = lo + (hi - lo) * frac
                return _safe_int(val)
            acc = nxt
        # Fallback to top edge
        return _safe_int(edges[-1])


# -------------------------------
# Watermark logic
# -------------------------------

@dataclass
class WatermarkConfig:
    # Absolute lower bound for the floor (safety)
    min_floor_wei: int = 1_000_000_000  # 1 gwei
    # EMA smoothing (0..1], higher -> faster reaction
    ema_alpha: float = 0.25
    # Histogram config
    hist_min_wei: int = 1
    hist_max_wei: int = 10**12
    hist_bins: int = 96
    hist_decay_per_block: float = 0.95

    # Hysteresis on utilization
    low_util: float = 0.60
    high_util: float = 0.90

    # Percentiles used at various pressures
    admit_quantile_low: float = 0.05   # at/below low_util
    evict_quantile_mid: float = 0.15   # mid band
    evict_quantile_high: float = 0.30  # above high_util

    # Smoothing limits per update (ratios)
    max_step_up: float = 1.50   # at most +50% per thresholds() call
    max_step_down: float = 0.67 # at most -33% per thresholds() call


@dataclass
class Thresholds:
    admit_floor_wei: int
    evict_below_wei: int  # 0 means "no eviction pressure"
    utilization: float


class FeeWatermark:
    """
    Rolling fee watermark with EMA & log-histogram.

    Typical call sequence (per node):
        wm = FeeWatermark(WatermarkConfig())
        ...
        wm.observe_admission(effective_fee_wei)
        ...
        wm.observe_block_inclusions(list_of_effective_fees_wei)
        ...
        th = wm.thresholds(pool_size, capacity)
    """

    __slots__ = ("cfg", "_hist", "_floor_ema", "_admit_floor", "_evict_below",
                 "_last_update_t")

    def __init__(self, cfg: Optional[WatermarkConfig] = None) -> None:
        self.cfg = cfg or WatermarkConfig()
        self._hist = LogHistogram(
            min_wei=self.cfg.hist_min_wei,
            max_wei=self.cfg.hist_max_wei,
            bins=self.cfg.hist_bins,
            decay=self.cfg.hist_decay_per_block,
        )
        self._floor_ema: float = float(self.cfg.min_floor_wei)
        self._admit_floor: int = self.cfg.min_floor_wei
        self._evict_below: int = 0
        self._last_update_t: float = time.monotonic()

    # ---------- observation hooks ----------

    def observe_admission(self, effective_fee_wei: int, *, weight: float = 1.0) -> None:
        """Record an admitted tx's effective fee into the histogram."""
        self._hist.observe(effective_fee_wei, weight=weight)

    def observe_block_inclusions(self, effective_fees_wei: Iterable[int]) -> None:
        """
        Record the fees of txs included in the latest block and update the EMA floor.
        Also decays the histogram to age out stale observations.
        """
        # Age out old samples first (block boundary)
        self._hist.decay()

        fees = [int(f) for f in effective_fees_wei if int(f) > 0]
        if not fees:
            return

        # Feed the histogram too (what the chain accepted recently matters).
        for f in fees:
            self._hist.observe(f, weight=1.0)

        # Target for EMA: a "conservative lower bound" of what clears.
        # Use the 20th percentile of block inclusions, clamped by min.
        fees_sorted = sorted(fees)
        p20_idx = max(0, min(len(fees_sorted) - 1, int(0.20 * (len(fees_sorted) - 1))))
        p20 = fees_sorted[p20_idx]
        target = max(self.cfg.min_floor_wei, p20)

        a = _clamp(self.cfg.ema_alpha, 0.01, 1.0)
        self._floor_ema = (1.0 - a) * self._floor_ema + a * float(target)

    # ---------- threshold computation ----------

    def _bounded_step(self, current: int, target: int) -> int:
        """Apply asymmetric step-size bounds to avoid oscillation."""
        if current <= 0:
            return max(target, 0)
        if target >= current:
            # step up is limited by ratio
            limit = int(math.ceil(current * self.cfg.max_step_up))
            return min(target, limit)
        else:
            # step down limited (cannot drop too fast)
            limit = int(math.floor(current * self.cfg.max_step_down))
            return max(target, limit)

    def thresholds(self, pool_size: int, capacity: int, *, now: Optional[float] = None) -> Thresholds:
        """
        Compute current thresholds given the pool occupancy.

        Returns a Thresholds struct with:
          - admit_floor_wei
          - evict_below_wei (0 means 'no eviction')
        """
        if capacity <= 0:
            util = 0.0
        else:
            util = _clamp(float(pool_size) / float(capacity), 0.0, 1.0)

        # Base floors from EMA and absolute minimum
        ema_floor = max(self.cfg.min_floor_wei, _safe_int(self._floor_ema))

        # Histogram-guided thresholds
        if util <= self.cfg.low_util:
            # Plenty of room: admit floor may relax toward lower quantiles.
            admit_q = self.cfg.admit_quantile_low
            admit_hist = self._hist.percentile(admit_q)
            admit_target = max(self.cfg.min_floor_wei, min(ema_floor, admit_hist))
            evict_target = 0  # no eviction pressure
        elif util >= self.cfg.high_util:
            # High pressure: raise thresholds toward higher percentiles.
            evict_q = self.cfg.evict_quantile_high
            evict_hist = self._hist.percentile(evict_q)
            # Admission shouldn't be weaker than eviction threshold.
            admit_target = max(ema_floor, evict_hist)
            evict_target = max(ema_floor, evict_hist)
        else:
            # Mid band: blend between low and high behaviors.
            t = (util - self.cfg.low_util) / max(1e-9, (self.cfg.high_util - self.cfg.low_util))
            evict_q = _lerp(self.cfg.evict_quantile_mid, self.cfg.evict_quantile_high, t)
            evict_hist = self._hist.percentile(evict_q)

            low_q = self.cfg.admit_quantile_low
            mid_q = self.cfg.evict_quantile_mid
            admit_q = _lerp(low_q, mid_q, t * 0.6)
            admit_hist = self._hist.percentile(admit_q)

            admit_target = max(self.cfg.min_floor_wei, max(ema_floor, min(evict_hist, max(admit_hist, ema_floor))))
            evict_target = max(self.cfg.min_floor_wei, int(evict_hist))

        # Smooth against last published thresholds
        new_admit = self._bounded_step(self._admit_floor, int(admit_target))
        new_evict = self._bounded_step(max(self._evict_below, 0), int(evict_target)) if evict_target > 0 else 0

        # Monotonic safety: never set evict below admit in the same tick
        if new_evict > 0 and new_evict < new_admit:
            new_evict = new_admit

        self._admit_floor = new_admit
        self._evict_below = new_evict
        self._last_update_t = time.monotonic() if now is None else now

        return Thresholds(admit_floor_wei=self._admit_floor, evict_below_wei=self._evict_below, utilization=util)

    # ---------- inspection / control ----------

    @property
    def floor_ema_wei(self) -> int:
        return _safe_int(self._floor_ema)

    def snapshot(self) -> dict:
        return {
            "floor_ema_wei": self.floor_ema_wei,
            "admit_floor_wei": int(self._admit_floor),
            "evict_below_wei": int(self._evict_below),
            "config": self.cfg.__dict__.copy(),
        }


# -------------------------------
# Minimal demonstration
# -------------------------------

if __name__ == "__main__":
    wm = FeeWatermark()
    # Simulate some low-fee admissions
    for f in [1_2e9, 1_1e9, 9_5e8, 1_0e9]:
        wm.observe_admission(int(f))

    # Block includes moderate fees
    wm.observe_block_inclusions([1_5e9, 1_6e9, 1_4e9, 1_7e9])

    # Pool thresholds at different utilizations
    for util in [0.3, 0.7, 0.95]:
        th = wm.thresholds(pool_size=int(util * 10_000), capacity=10_000)
        print(f"util={util:.2f} -> admit_floor={th.admit_floor_wei} evict_below={th.evict_below_wei}")
