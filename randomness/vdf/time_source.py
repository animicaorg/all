"""
randomness.vdf.time_source
=========================

Helpers for *non-consensus* calibration between VDF iterations (T) and
wall-clock time on the current machine. These utilities are intended for
UX/mining hints (e.g., picking a target-iteration count that lands near a
desired delay), dashboards, and tests. They MUST NOT be used for any
consensus-critical decision.

Approach
--------
We approximate the Wesolowski prover's hot loop with repeated modular
squarings:
    x <- x^2 mod N     (repeated T times)
Using the consensus modulus N (if available) gives a closer proxy for
real costs; otherwise we fall back to a locally derived 2048-bit modulus.
We time how many iterations fit into a short interval to estimate
iterations-per-second (ips). Results can be smoothed with a simple EMA.

Public API
----------
- measure_iterations_per_second(...): one-shot throughput probe.
- estimate_iterations_for_seconds(target_s, ips): convert time -> T.
- estimate_seconds_for_iterations(T, ips): convert T -> time.
- RateEMA(alpha): tiny EMA helper for smoothing noisy measurements.
- TimeSource(...): convenience wrapper with caching and smoothing.

Notes
-----
- The *verifier* runtime is not directly modeled here—this module focuses on
  prover-like squaring throughput. That's sufficient for coarse iteration↔time
  calibration commonly needed by operators.
- Keep runs short (default ~0.5s probe) to limit blocking.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

# --- Soft imports of consensus params (optional) ---------------------------------
try:
    from .params import get_params  # type: ignore
except Exception:  # pragma: no cover

    def get_params():
        class _P:
            iterations = 1
            # Fallback modulus is fine for timing loops; security irrelevant here.
            modulus_n = (1 << 2048) - 159
            backend = "rsa"

        return _P()


# --- Internal helpers ------------------------------------------------------------


def _be_u64(n: int) -> bytes:
    return int(n).to_bytes(8, "big", signed=False)


def _fallback_modulus(bits: int = 2048) -> int:
    # Deterministic, non-cryptographic large odd modulus for timing only.
    base = int.from_bytes(b"ANIMICA_VDF_TIME_SOURCE\x00", "big")
    n = (base << (bits - base.bit_length())) | 1
    return n


def _modulus_for_timing() -> int:
    try:
        n = int(get_params().modulus_n)
        if n > 3:
            return n
    except Exception:
        pass
    return _fallback_modulus(2048)


def _squarings(n: int, x: int, T: int) -> int:
    """Perform T modular squarings mod n (approximate prover hot loop)."""
    for _ in range(T):
        x = (x * x) % n
    return x


def _measure_once(
    n: int, warmup_squarings: int, window_seconds: float
) -> Tuple[int, float]:
    # Warmup to stabilize caches/JITs (if any).
    x = 5
    if warmup_squarings > 0:
        x = _squarings(n, x, warmup_squarings)
    # Timed loop: adaptively batch squarings to avoid Python loop overhead dominating.
    batch = 1024
    count = 0
    start = time.perf_counter()
    deadline = start + max(0.05, float(window_seconds))
    now = start
    while now < deadline:
        x = _squarings(n, x, batch)
        count += batch
        now = time.perf_counter()
        # crude adaptation if we are overshooting or undershooting badly
        elapsed = now - start
        if elapsed > 0:
            # aim ~100 batches per window
            target_batch = max(64, min(1 << 20, int(count / (elapsed * 100.0))))
            # smooth batch size to avoid thrashing
            batch = (batch * 3 + target_batch) // 4 or 64
    elapsed = now - start
    # Prevent "unused variable" complaints and keep reference path deterministic
    _ = x
    return count, elapsed


# --- Public functions ------------------------------------------------------------


def measure_iterations_per_second(
    *,
    seconds: float = 0.5,
    warmup: int = 4096,
    rounds: int = 3,
) -> float:
    """
    Empirically measure modular-squaring iterations per second.

    Args:
        seconds: duration of each measurement window.
        warmup: number of warmup squarings before the first window.
        rounds: number of measurement windows to average.

    Returns:
        Estimated iterations per second (float). Never returns <= 0.
    """
    n = _modulus_for_timing()
    seconds = max(0.05, float(seconds))
    rounds = max(1, int(rounds))

    ips_values = []
    for i in range(rounds):
        wu = warmup if i == 0 else 0
        count, elapsed = _measure_once(n, wu, seconds)
        if elapsed <= 0:
            continue
        ips_values.append(count / elapsed)

    if not ips_values:
        return 1.0  # ultra conservative fallback

    # Use median-of-three style robustness if rounds >= 3
    ips_values.sort()
    if len(ips_values) >= 3:
        mid = ips_values[len(ips_values) // 2]
        return max(1.0, float(mid))
    return max(1.0, float(sum(ips_values) / len(ips_values)))


def estimate_iterations_for_seconds(target_seconds: float, ips: float) -> int:
    """Given a target wall time and iterations/sec, return an iteration count."""
    target_seconds = max(0.0, float(target_seconds))
    ips = max(1.0, float(ips))
    # Round to nearest multiple of 2 to align with common VDF iteration parity conventions.
    T = int(target_seconds * ips)
    return max(1, (T + 1) // 2 * 2)


def estimate_seconds_for_iterations(iterations: int, ips: float) -> float:
    """Estimate wall time for an iteration count given iterations/sec."""
    iterations = max(0, int(iterations))
    ips = max(1.0, float(ips))
    return iterations / ips


# --- Simple EMA smoother ---------------------------------------------------------


@dataclass
class RateEMA:
    """Exponential moving average for iterations/sec measurements."""

    alpha: float = 0.2
    value: Optional[float] = None

    def update(self, sample_ips: float) -> float:
        sample_ips = max(1.0, float(sample_ips))
        if self.value is None:
            self.value = sample_ips
        else:
            a = max(0.0, min(1.0, float(self.alpha)))
            self.value = (1.0 - a) * float(self.value) + a * sample_ips
        return float(self.value)


# --- TimeSource convenience wrapper ---------------------------------------------


@dataclass
class TimeSource:
    """
    Convenience wrapper that tracks a smoothed iterations/sec and can
    persist it to a small JSON file between runs (optional).
    """

    alpha: float = 0.2
    cache_path: Optional[str] = None
    ema: RateEMA = None  # type: ignore

    def __post_init__(self) -> None:
        self.ema = RateEMA(alpha=self.alpha)
        if self.cache_path and os.path.isfile(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                val = float(data.get("ips", 0.0))
                if val > 0:
                    self.ema.value = val
            except Exception:
                pass

    # Calibration / persistence

    def calibrate(
        self, seconds: float = 0.5, warmup: int = 4096, rounds: int = 3
    ) -> float:
        sample = measure_iterations_per_second(
            seconds=seconds, warmup=warmup, rounds=rounds
        )
        ips = self.ema.update(sample)
        self._persist()
        return ips

    def _persist(self) -> None:
        if not self.cache_path:
            return
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"ips": self.ema.value}, f)
        except Exception:
            pass

    # Conversions

    @property
    def ips(self) -> float:
        """Current smoothed iterations/sec (or 1.0 if unset)."""
        return float(self.ema.value) if self.ema.value and self.ema.value > 0 else 1.0

    def iters_for(self, seconds: float) -> int:
        return estimate_iterations_for_seconds(seconds, self.ips)

    def seconds_for(self, iterations: int) -> float:
        return estimate_seconds_for_iterations(iterations, self.ips)


__all__ = [
    "measure_iterations_per_second",
    "estimate_iterations_for_seconds",
    "estimate_seconds_for_iterations",
    "RateEMA",
    "TimeSource",
]
