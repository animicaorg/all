"""
Animica | proofs.utils.math

Numerical helpers used across proof verifiers:

- Safe logarithms / exponentials with domain guards
- clamp / lerp primitives
- Fixed-point conversions for micro-nats (µnats): 1 nat = 1_000_000 µnats
- Ratio helpers (with zero/inf handling), including fixed-point variants
- Convenience H(u) = −ln(u) with strict domain checks for u-draws

These functions are *deterministic* and avoid surprises from NaNs/inf by
explicitly validating inputs. Python integers are unbounded, so µnats are
represented as `int` for exact arithmetic where possible.

This module intentionally has **no** dependencies on consensus/ to avoid cycles.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Iterable, Optional, Tuple

# ------------------------------------------------------------------------------
# Constants & guards
# ------------------------------------------------------------------------------

# Minimum positive value accepted by safe ln to avoid domain/underflow surprises
LN_MIN_POS = 1e-300  # well within double range without flushing to 0
# Clamp upper bound for log arguments when using log1p-style helpers
LN_MAX = 1e300

# µnats fixed-point scale
MUNATS_PER_NAT = 1_000_000
NATS_PER_MUNAT = 1.0 / MUNATS_PER_NAT


# ------------------------------------------------------------------------------
# Basic guards & clamps
# ------------------------------------------------------------------------------


def clamp(x: float, lo: float, hi: float) -> float:
    """Clamp x into [lo, hi]. Requires lo <= hi."""
    if lo > hi:
        raise ValueError("clamp: lo > hi")
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def is_finite(x: float) -> bool:
    """True iff x is a finite real number."""
    return math.isfinite(x)


# ------------------------------------------------------------------------------
# Log/exp helpers (safe)
# ------------------------------------------------------------------------------


def ln_safe(x: float) -> float:
    """
    Natural log with input clamped into [LN_MIN_POS, LN_MAX].
    Raises if x is NaN or negative; 0 is promoted to LN_MIN_POS.
    """
    if not is_finite(x):
        raise ValueError("ln_safe: non-finite input")
    if x < 0.0:
        raise ValueError("ln_safe: negative input")
    xx = clamp(x, LN_MIN_POS, LN_MAX)
    return math.log(xx)


def log1p_safe(x: float) -> float:
    """
    log(1+x) with guard: require x > -1, clamp extremely large x to LN_MAX-1.
    """
    if not is_finite(x):
        raise ValueError("log1p_safe: non-finite input")
    if x <= -1.0:
        raise ValueError("log1p_safe: x <= -1")
    xx = clamp(x, -0.999999999999, LN_MAX - 1.0)
    return math.log1p(xx)


def exp_safe(x: float) -> float:
    """
    exp(x) with range clamps to avoid OverflowError. Returns a finite value in (0, +inf),
    clamped to about 1e308 on overflow and to ~0 on very negative inputs.
    """
    if not is_finite(x):
        raise ValueError("exp_safe: non-finite input")
    # Reasonable clamps within IEEE754 double range
    xx = clamp(x, -700.0, 700.0)
    return math.exp(xx)


# ------------------------------------------------------------------------------
# PoIES draw helper
# ------------------------------------------------------------------------------


def H_of_u(u: float) -> float:
    """
    H(u) = −ln(u) used for PoIES u-draws.
    Requires 0 < u <= 1. Values extremely close to 0 are promoted to LN_MIN_POS.
    """
    if not is_finite(u):
        raise ValueError("H_of_u: non-finite u")
    if u <= 0.0 or u > 1.0:
        raise ValueError("H_of_u: u must be in (0, 1]")
    uu = max(u, LN_MIN_POS)
    return -math.log(uu)


# ------------------------------------------------------------------------------
# µnats fixed-point conversions
# ------------------------------------------------------------------------------


def to_munats(nats: float, *, rounding: str = "nearest") -> int:
    """
    Convert floating-point nats → integer micro-nats (µnats).
    rounding ∈ {"nearest", "floor", "ceil"}.
    """
    if not is_finite(nats):
        raise ValueError("to_munats: non-finite input")
    scaled = nats * MUNATS_PER_NAT
    if rounding == "nearest":
        return int(round(scaled))
    if rounding == "floor":
        return math.floor(scaled)
    if rounding == "ceil":
        return math.ceil(scaled)
    raise ValueError("to_munats: invalid rounding mode")


def from_munats(mn: int) -> float:
    """Convert integer micro-nats → floating-point nats."""
    if not isinstance(mn, int):
        raise TypeError("from_munats: expects int")
    return mn * NATS_PER_MUNAT


def add_munats(a: int, b: int) -> int:
    """Exact addition in µnats (Python int is unbounded)."""
    return a + b


def sub_munats(a: int, b: int) -> int:
    """Exact subtraction in µnats; may be negative."""
    return a - b


def sum_munats(values: Iterable[int]) -> int:
    """Exact sum of an iterable of µnats."""
    total = 0
    for v in values:
        if not isinstance(v, int):
            raise TypeError("sum_munats: non-int element")
        total += v
    return total


# ------------------------------------------------------------------------------
# Ratios & scaling
# ------------------------------------------------------------------------------


def ratio(dividend: float, divisor: float, *, default: float = 0.0) -> float:
    """
    Return dividend / divisor with safe zero handling.
    - If divisor == 0:
        - If dividend == 0 → return `default` (usually 0.0)
        - Else → return +inf (clamped to a large finite sentinel)
    """
    if not (is_finite(dividend) and is_finite(divisor)):
        raise ValueError("ratio: non-finite input")
    if divisor == 0.0:
        if dividend == 0.0:
            return default
        # Clamp "infinite" ratio to a large sentinel to keep math finite downstream
        return 1e300
    return dividend / divisor


def ratio_clamped(
    dividend: float, divisor: float, lo: float, hi: float, *, default: float = 0.0
) -> float:
    """ratio() followed by clamp into [lo, hi]."""
    return clamp(ratio(dividend, divisor, default=default), lo, hi)


def ratio_fp(
    dividend: int, divisor: int, *, scale: int = 1_000_000, default: int = 0
) -> int:
    """
    Fixed-point ratio using integers:
        floor((dividend * scale) / divisor)
    If divisor == 0 → returns default (usually 0).
    """
    if divisor == 0:
        return default
    if divisor < 0:
        # Normalize signs for consistency
        dividend, divisor = -dividend, -divisor
    # Use integer math for determinism/reproducibility
    return (dividend * scale) // divisor


def mul_ratio_clamped(x: float, num: float, den: float, lo: float, hi: float) -> float:
    """
    Compute x * (num/den) safely and clamp to [lo, hi].
    """
    r = ratio(num, den, default=0.0)
    return clamp(x * r, lo, hi)


def harmonic_mean(
    values: Iterable[float],
    weights: Optional[Iterable[float]] = None,
    *,
    eps: float = 1e-18,
) -> float:
    """
    Stable harmonic mean with optional positive weights.
    Returns 0 for empty iterables.
    """
    vals = list(values)
    if not vals:
        return 0.0
    if weights is None:
        denom = 0.0
        for v in vals:
            denom += ratio(1.0, v + eps, default=0.0)
        return len(vals) / denom if denom > 0.0 else 0.0
    ws = list(weights)
    if len(ws) != len(vals):
        raise ValueError("harmonic_mean: len(weights) != len(values)")
    wsum = sum(ws)
    if wsum <= 0:
        return 0.0
    denom = 0.0
    for w, v in zip(ws, vals):
        denom += w * ratio(1.0, v + eps, default=0.0)
    return wsum / denom if denom > 0.0 else 0.0


# ------------------------------------------------------------------------------
# Exact rational helpers when needed
# ------------------------------------------------------------------------------


def ratio_fraction(dividend: int, divisor: int) -> Fraction:
    """
    Return an exact Fraction dividend/divisor.
    Raises ZeroDivisionError if divisor == 0.
    """
    return Fraction(dividend, divisor)


# ------------------------------------------------------------------------------
# Quick self-checks (not executed under coverage)
# ------------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Clamp & ln
    assert clamp(2.0, 0.0, 1.0) == 1.0
    assert abs(ln_safe(1.0) - 0.0) < 1e-15
    try:
        ln_safe(float("nan"))
        raise AssertionError("expected error")
    except ValueError:
        pass

    # H(u)
    hu = H_of_u(0.5)
    assert 0.69 < hu < 0.70

    # µnats
    mn = to_munats(1.234567)
    assert from_munats(mn) == mn * NATS_PER_MUNAT

    # Ratios
    assert ratio(10.0, 2.0) == 5.0
    assert ratio(0.0, 0.0, default=0.0) == 0.0
    assert ratio_fp(3, 2, scale=1000) == 1500

    # Harmonic mean
    hm = harmonic_mean([1.0, 2.0, 4.0])
    assert 1.71 < hm < 1.72  # ~1.7142857

    print("math utils OK")
