"""
Animica • DA • Sampling probability helpers

This module provides small, dependency-free utilities to reason about
Data Availability Sampling (DAS) probabilities.

Key ideas
---------
- p_fail is the probability that a verifier *misses* any corrupted share
  during sampling.
- If at least *C* out of *N* shares are corrupted, sampling *n* unique
  indices *without replacement* has miss probability:

      p_fail = C(N - C, n) / C(N, n)

  where C(a, b) is "a choose b". This is exact for unique-index plans.
- A conservative *with-replacement* upper bound is:

      p_fail <= (1 - C/N) ** n

- If the corruption level is unknown, a safe lower bound is C = 1
  ("at least one bad share exists"). That yields:
    • Without replacement: p_fail = (N - n) / N
    • With replacement:   p_fail = ((N - 1) / N) ** n

APIs
----
- estimate_p_fail_upper(N, n, assumed_corrupt_fraction=None, without_replacement=True)
- required_samples_for_target_pfail(N, target_p_fail, assumed_corrupt_fraction, without_replacement=True)
- hypergeom_miss_prob(N, C, n)
- replacement_miss_prob(N, C, n)

Notes
-----
These helpers are intentionally simple and numerically stable for the ranges
we expect in DAS. The hypergeometric form uses `math.comb` and short-circuits
for trivial cases.
"""

from __future__ import annotations

import math
from typing import Optional

# ---------------------------- Core formulas --------------------------------


def hypergeom_miss_prob(N: int, C: int, n: int) -> float:
    """
    Probability of missing all C corrupted items when sampling n without replacement from N.

    p = comb(N - C, n) / comb(N, n)

    Edge handling:
      - If n <= 0: return 1.0 (no samples ⇒ always miss)
      - If C <= 0: return 1.0 (no corruption ⇒ "miss" is vacuously 1.0; callers usually clamp)
      - If n > N - C: return 0.0 (you inevitably hit corruption)
      - If n >= N: return 0.0
    """
    N = int(N)
    C = int(C)
    n = int(n)
    if n <= 0:
        return 1.0
    if C <= 0:
        return 1.0
    if C >= N:
        return 0.0
    if n >= N:
        return 0.0
    if n > N - C:
        return 0.0
    # Use math.comb for exact integers; convert to float at the end.
    num = math.comb(N - C, n)
    den = math.comb(N, n)
    return float(num / den)


def replacement_miss_prob(N: int, C: int, n: int) -> float:
    """
    With-replacement upper bound: (1 - C/N) ** n

    Edge handling mirrors `hypergeom_miss_prob`.
    """
    N = int(N)
    C = int(C)
    n = int(n)
    if n <= 0:
        return 1.0
    if C <= 0:
        return 1.0
    if C >= N:
        return 0.0
    return float((1.0 - (C / float(N))) ** n)


# ---------------------------- Public helpers -------------------------------


def estimate_p_fail_upper(
    *,
    population_size: int,
    sample_count: int,
    assumed_corrupt_fraction: Optional[float] = None,
    without_replacement: bool = True,
) -> float:
    """
    Upper-bound the probability of *missing* any corruption.

    Parameters
    ----------
    population_size : N
    sample_count    : n
    assumed_corrupt_fraction :
        If None, assume the weakest non-trivial corruption level:
        at least one bad share (C = 1).
        If provided:
          - If >= 1.0, treated as fraction of N (clamped to N-1).
          - If between 0 and 1, treated as fraction f and converted to C = ceil(f*N).
          - If >= 1 and integer, also accepted as a "count" (C shares).

    without_replacement : use the exact hypergeometric miss probability when True,
                          else use the with-replacement bound.

    Returns
    -------
    float in [0, 1]
    """
    N = int(population_size)
    n = int(sample_count)
    if N <= 0:
        return 1.0
    if n <= 0:
        return 1.0

    # Determine corrupted count C
    if assumed_corrupt_fraction is None:
        C = 1
    else:
        f = float(assumed_corrupt_fraction)
        if f <= 0.0:
            C = 0
        elif f < 1.0:
            C = max(1, int(math.ceil(f * N)))
        else:
            # Treat as "count" if integer-like; otherwise clamp to N-1 as fraction
            # (kept for flexibility; docstring prioritizes fraction semantics).
            C = int(min(f, N - 1))

    if C <= 0:
        return 1.0
    if C >= N:
        return 0.0

    if without_replacement:
        return hypergeom_miss_prob(N, C, n)
    else:
        return replacement_miss_prob(N, C, n)


def required_samples_for_target_pfail(
    *,
    population_size: int,
    target_p_fail: float,
    assumed_corrupt_fraction: float,
    without_replacement: bool = True,
    max_cap: Optional[int] = None,
) -> int:
    """
    Smallest n such that estimate_p_fail_upper(...) <= target_p_fail.

    Parameters
    ----------
    population_size : N
    target_p_fail   : desired upper bound (e.g., 1e-9). Values <= 0 yield 0.
    assumed_corrupt_fraction :
        Fraction f in (0,1]; translated to a count C = ceil(f*N).
        (If you know C as a count already, pass f = C / N.)
    without_replacement :
        If True, use hypergeometric; else use with-replacement bound (faster closed form).
    max_cap :
        Optional hard cap for n to guard against pathologies; by default, N.

    Returns
    -------
    Integer n in [0, N]. If target is too low given f, returns N.
    """
    N = int(population_size)
    if N <= 0:
        return 0
    target = float(target_p_fail)
    if target <= 0.0:
        return 0
    if target >= 1.0:
        return 0

    # Convert fraction → count and clamp to [1, N-1]
    f = float(assumed_corrupt_fraction)
    if f <= 0.0:
        return 0  # no corruption assumed ⇒ no samples needed
    if f >= 1.0:
        return 0  # everything corrupt ⇒ sampling can't help; caller should treat as invalid
    C = max(1, min(N - 1, int(math.ceil(f * N))))
    cap = N if max_cap is None else int(min(max_cap, N))

    if without_replacement:
        # No simple closed form; binary search n ∈ [0, cap]
        lo, hi = 0, cap
        while lo < hi:
            mid = (lo + hi) // 2
            if hypergeom_miss_prob(N, C, mid) <= target:
                hi = mid
            else:
                lo = mid + 1
        return int(lo)
    else:
        # With-replacement admits a closed form:
        #   (1 - C/N)^n <= target  =>  n * ln(1 - C/N) <= ln(target)
        # Since ln(1 - x) < 0 for x in (0,1), divide by negative and flip sign.
        x = 1.0 - (C / float(N))
        # Guard numerical issues
        x = min(max(x, 1e-18), 1.0 - 1e-18)
        n_real = math.log(target) / math.log(x)
        n_int = int(math.ceil(max(0.0, n_real)))
        return int(min(n_int, cap))


# ---------------------------- Convenience ----------------------------------


def one_bad_share_bound(N: int, n: int, *, without_replacement: bool = True) -> float:
    """
    Shortcut for the "at least one bad share" scenario (C = 1).
    """
    if without_replacement:
        # exact: (N - n) / N  for 0 <= n <= N
        if N <= 0:
            return 1.0
        n = max(0, min(int(n), int(N)))
        return float((N - n) / float(N))
    else:
        if N <= 0:
            return 1.0
        return ((N - 1) / float(N)) ** int(n)


__all__ = [
    "hypergeom_miss_prob",
    "replacement_miss_prob",
    "estimate_p_fail_upper",
    "required_samples_for_target_pfail",
    "one_bad_share_bound",
]
