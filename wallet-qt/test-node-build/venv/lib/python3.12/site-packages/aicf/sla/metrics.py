from __future__ import annotations

"""
Per-job and per-provider SLA metric helpers.

This module is pure-Python and IO-free. It provides:
  - JobMeasure: one record of measured outcomes for a completed job.
  - AvailabilityTracker: heartbeat → availability over a sliding window.
  - ProviderMetricsWindow: rolling aggregates (success rate, QoS/traps averages,
    latency histograms → p50/p95/p99).

Intended consumers:
  * aicf.sla.evaluate_sla(...) – uses these aggregates to derive severity.
  * aicf.registry.heartbeat – feeds heartbeats into AvailabilityTracker.
  * aicf.queue/dispatcher – records JobMeasure after completion.

All values are designed to be deterministic and bounded for stable policy mapping.
"""


import bisect
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Iterable, List, Optional, Tuple

# ---------------------------
# Utilities
# ---------------------------


def clamp01(x: float) -> float:
    if math.isnan(x):
        return 0.0
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def ewma(prev: Optional[float], new: float, alpha: float) -> float:
    """Exponential weighted moving average (alpha in (0,1])."""
    if prev is None:
        return new
    return (1.0 - alpha) * prev + alpha * new


# ---------------------------
# Per-Job measurement
# ---------------------------


@dataclass(frozen=True)
class JobMeasure:
    """
    One completed job's measured outcomes.

    Fields:
      - success: True if the job completed and passed provider-side checks.
      - traps_ratio: fraction of trap tests passed (0..1). None if N/A.
      - qos_score: quality-of-service score (0..1). None if N/A.
      - latency_ms: end-to-end latency in milliseconds (non-negative).
      - timestamp_s: UNIX seconds when job completed (float allowed).
    """

    success: bool
    traps_ratio: Optional[float]
    qos_score: Optional[float]
    latency_ms: int
    timestamp_s: float

    def normalized(self) -> "JobMeasure":
        """Clamp ratios to [0,1], non-negative latency."""
        tr = None if self.traps_ratio is None else clamp01(self.traps_ratio)
        qos = None if self.qos_score is None else clamp01(self.qos_score)
        lat = 0 if self.latency_ms < 0 else int(self.latency_ms)
        return JobMeasure(self.success, tr, qos, lat, float(self.timestamp_s))


# ---------------------------
# Availability tracker
# ---------------------------


class AvailabilityTracker:
    """
    Tracks heartbeats and computes availability over a sliding window.

    Model:
      Each heartbeat at time t covers the interval [t, t + ttl_s].
      Availability over [now - window_s, now] is the union length of covered
      intervals intersected with the window, divided by window length.

    This yields a robust estimate without requiring explicit online/offline
    toggles and tolerates bursty heartbeat schedules.
    """

    def __init__(self, *, ttl_s: float = 60.0):
        if ttl_s <= 0:
            raise ValueError("ttl_s must be > 0")
        self.ttl_s = float(ttl_s)
        self._beats: Deque[float] = deque()

    def heartbeat(self, t_s: float) -> None:
        """Record a heartbeat timestamp (seconds)."""
        t = float(t_s)
        # Keep monotonic-ish; allow out-of-order but insert sorted cheaply by appending then sort if tiny disorder.
        if not self._beats or t >= self._beats[-1]:
            self._beats.append(t)
        else:
            # rare path: maintain sorted deque by insertion (small N typical)
            idx = bisect.bisect_left(list(self._beats), t)
            self._beats.insert(idx, t)

    def _prune_older_than(self, cutoff_s: float) -> None:
        while self._beats and self._beats[0] < cutoff_s:
            # We can drop beats that end before cutoff (t + ttl_s < cutoff) safely.
            if self._beats[0] + self.ttl_s <= cutoff_s:
                self._beats.popleft()
            else:
                break

    def availability(self, now_s: float, window_s: float) -> float:
        """
        Compute availability in [now - window_s, now].

        Returns:
            fraction in [0,1].
        """
        if window_s <= 0:
            return 0.0
        window_start = now_s - window_s
        # prune
        self._prune_older_than(window_start - self.ttl_s)  # conservative
        beats = list(self._beats)
        if not beats:
            return 0.0

        # Build union of intervals [t, t+ttl] ∩ [window_start, now]
        intervals: List[Tuple[float, float]] = []
        for t in beats:
            a = max(window_start, t)
            b = min(now_s, t + self.ttl_s)
            if b > a:
                intervals.append((a, b))
        if not intervals:
            return 0.0

        # Merge intervals
        intervals.sort()
        merged: List[Tuple[float, float]] = [intervals[0]]
        for a, b in intervals[1:]:
            la, lb = merged[-1]
            if a <= lb:
                merged[-1] = (la, max(lb, b))
            else:
                merged.append((a, b))

        covered = sum(b - a for a, b in merged)
        avail = clamp01(covered / window_s)
        return avail


# ---------------------------
# Provider metrics (rolling)
# ---------------------------


@dataclass
class ProviderSnapshot:
    """Point-in-time aggregates for SLA evaluation and dashboards."""

    window_s: float
    n_jobs: int
    success_rate: float  # 0..1
    traps_ratio_avg: Optional[float]  # 0..1 or None if no data
    qos_avg: Optional[float]  # 0..1 or None if no data
    latency_p50_ms: Optional[int]
    latency_p95_ms: Optional[int]
    latency_p99_ms: Optional[int]
    availability: float  # 0..1


class ProviderMetricsWindow:
    """
    Rolling aggregates over the last `window_s` seconds.

    Storage strategy: keep a deque of JobMeasure within window.
    Histograms and EWMAs are (re)computed on snapshot. This is simpler and
    perfectly adequate for provider-level throughput in dev/testnets, while
    keeping behavior deterministic and easy to reason about.
    """

    DEFAULT_LATENCY_BUCKETS_MS: Tuple[int, ...] = (
        25,
        50,
        75,
        100,
        150,
        200,
        300,
        400,
        500,
        750,
        1_000,
        1_500,
        2_000,
        3_000,
        5_000,
        7_500,
        10_000,
        15_000,
        20_000,
        30_000,
        60_000,
    )

    def __init__(
        self,
        *,
        window_s: float = 900.0,  # 15 minutes by default
        ewma_alpha: float = 0.2,
        latency_buckets_ms: Optional[Iterable[int]] = None,
        availability_ttl_s: float = 60.0,
    ):
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        if not (0 < ewma_alpha <= 1):
            raise ValueError("ewma_alpha must be in (0,1]")
        self.window_s = float(window_s)
        self.ewma_alpha = float(ewma_alpha)
        self.latency_edges: Tuple[int, ...] = (
            tuple(sorted(latency_buckets_ms))
            if latency_buckets_ms
            else self.DEFAULT_LATENCY_BUCKETS_MS
        )
        self._jobs: Deque[JobMeasure] = deque()
        # EWMAs (derived lazily on snapshot; stored here for continuity across windows)
        self._ewma_traps: Optional[float] = None
        self._ewma_qos: Optional[float] = None
        # Availability
        self._availability = AvailabilityTracker(ttl_s=availability_ttl_s)

    # ---- Recording ----

    def record_job(self, jm: JobMeasure) -> None:
        jm = jm.normalized()
        self._jobs.append(jm)
        # Update EWMAs incrementally
        if jm.traps_ratio is not None:
            self._ewma_traps = ewma(self._ewma_traps, jm.traps_ratio, self.ewma_alpha)
        if jm.qos_score is not None:
            self._ewma_qos = ewma(self._ewma_qos, jm.qos_score, self.ewma_alpha)

    def heartbeat(self, t_s: float) -> None:
        self._availability.heartbeat(t_s)

    # ---- Housekeeping ----

    def _prune(self, now_s: float) -> None:
        cutoff = now_s - self.window_s
        while self._jobs and self._jobs[0].timestamp_s < cutoff:
            self._jobs.popleft()

    # ---- Aggregation helpers ----

    def _latency_histogram(self, jobs: Iterable[JobMeasure]) -> List[int]:
        counts = [0] * (len(self.latency_edges) + 1)  # last bucket = > max(edge)
        for jm in jobs:
            lat = jm.latency_ms
            idx = bisect.bisect_right(self.latency_edges, lat)
            counts[idx] += 1
        return counts

    def _percentile_from_hist(self, hist: List[int], p: float) -> Optional[int]:
        """Return approximate percentile (0..100) in ms using bucket midpoints."""
        total = sum(hist)
        if total == 0:
            return None
        target = max(0, min(total - 1, int(math.ceil(p / 100.0 * total) - 1)))
        # Walk cumulative
        cum = 0
        for i, c in enumerate(hist):
            if c == 0:
                continue
            prev = cum
            cum += c
            if cum > target:
                # Bucket bounds
                if i == 0:
                    lo = 0
                    hi = self.latency_edges[0]
                elif i == len(hist) - 1:
                    lo = self.latency_edges[-1]
                    hi = max(lo * 2, lo + 1)  # unbounded; choose a sane extrapolation
                else:
                    lo = self.latency_edges[i - 1]
                    hi = self.latency_edges[i]
                # Linear interpolation within bucket
                frac_in_bucket = (target - prev) / c
                approx = int(round(lo + (hi - lo) * frac_in_bucket))
                return approx
        return None

    # ---- Snapshots ----

    def snapshot(self, now_s: float) -> ProviderSnapshot:
        """Compute aggregates over the active window ending at now_s."""
        self._prune(now_s)
        jobs = list(self._jobs)
        n = len(jobs)
        if n == 0:
            return ProviderSnapshot(
                window_s=self.window_s,
                n_jobs=0,
                success_rate=0.0,
                traps_ratio_avg=(
                    self._ewma_traps if self._ewma_traps is not None else None
                ),
                qos_avg=self._ewma_qos if self._ewma_qos is not None else None,
                latency_p50_ms=None,
                latency_p95_ms=None,
                latency_p99_ms=None,
                availability=self._availability.availability(now_s, self.window_s),
            )

        # Success rate
        succ = sum(1 for jm in jobs if jm.success)
        success_rate = succ / n

        # Averages (simple mean over window; EWMA exposed separately if needed)
        traps_vals = [jm.traps_ratio for jm in jobs if jm.traps_ratio is not None]
        qos_vals = [jm.qos_score for jm in jobs if jm.qos_score is not None]
        traps_avg = (
            clamp01(sum(traps_vals) / len(traps_vals))
            if traps_vals
            else (self._ewma_traps if self._ewma_traps is not None else None)
        )
        qos_avg = (
            clamp01(sum(qos_vals) / len(qos_vals))
            if qos_vals
            else (self._ewma_qos if self._ewma_qos is not None else None)
        )

        # Latency
        hist = self._latency_histogram(jobs)
        p50 = self._percentile_from_hist(hist, 50.0)
        p95 = self._percentile_from_hist(hist, 95.0)
        p99 = self._percentile_from_hist(hist, 99.0)

        return ProviderSnapshot(
            window_s=self.window_s,
            n_jobs=n,
            success_rate=clamp01(success_rate),
            traps_ratio_avg=traps_avg,
            qos_avg=qos_avg,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
            latency_p99_ms=p99,
            availability=self._availability.availability(now_s, self.window_s),
        )

    # ---- Convenience getters commonly used by SLA mapping ----

    def latency_p99_ms(self, now_s: float) -> Optional[int]:
        return self.snapshot(now_s).latency_p99_ms

    def availability(self, now_s: float) -> float:
        return self.snapshot(now_s).availability


# ---------------------------
# Minimal deterministic tests (optional self-check)
# ---------------------------

if __name__ == "__main__":
    # Simple smoke check
    pm = ProviderMetricsWindow(window_s=60.0)
    t0 = 1_000_000.0
    pm.heartbeat(t0)
    for i in range(50):
        pm.record_job(
            JobMeasure(
                True,
                traps_ratio=0.9,
                qos_score=0.85,
                latency_ms=100 + i,
                timestamp_s=t0 + i,
            )
        )
    snap = pm.snapshot(t0 + 59.0)
    print(
        "n_jobs",
        snap.n_jobs,
        "succ",
        snap.success_rate,
        "p99",
        snap.latency_p99_ms,
        "avail",
        snap.availability,
    )
