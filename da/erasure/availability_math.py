"""
Animica • DA • Erasure — Availability Sampling Math

This module provides compact, well-documented helpers to reason about
Data-Availability Sampling (DAS) for the **row-wise RS(k, n) layout** used in
Animica's DA layer (see `da/erasure/*`). It models an adversary that withholds
a number of leaves (shares). If any stripe (row) ends up with < k available
leaves, the blob becomes unrecoverable. Randomly sampling leaves across the
entire matrix detects such withholding with probability that grows with the
number of samples.

We give:
  • Hypergeometric & binomial (with-replacement) miss probabilities.
  • Closed-form sample sizing for a target failure probability p_fail.
  • Worst-case “minimal-withholding” models (break 1 or more stripes).
  • Friendly dataclasses for returning sizing plans.

The formulas are conservative and easy to audit. They intentionally ignore any
additional structure (e.g., NMT range-proofs or multi-dimensional encodings)
so they can serve as safe defaults and unit-test anchors.

Notation
--------
- total leaves  T = stripes * n
- data shards   k, parity p, total n = k + p
- minimal leaves to break one stripe: (n - k + 1)
- bad leaves (withheld) overall: B
- bad fraction: f = B / T
- samples drawn uniformly at random: s

Miss probabilities
------------------
- Without replacement (hypergeometric):
      P_miss = C(T-B, s) / C(T, s)
- With replacement (binomial approximation):
      P_miss ≈ (1 - f)^s

Closed-form sizing (with replacement):
      s ≥ ceil( log(p_fail) / log(1 - f) )

Edge cases:
- If B == 0 (no attack), detection is undefined — a “miss” is vacuous.
  We return P_miss = 1 by convention and flag that there is nothing to detect.
- If f == 0, any closed-form will raise; we special-case it.

See also:
- `da/erasure/partitioner.py` & `encoder.py` for how leaves are laid out.
- `da/nmt/*` for how sampling proofs are verified.

"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .params import ErasureParams


# --------------------------------------------------------------------------- #
# Combinatorics
# --------------------------------------------------------------------------- #

def _comb(n: int, k: int) -> int:
    """Safe math.comb wrapper with small guards."""
    if k < 0 or k > n:
        return 0
    return math.comb(n, k)


def miss_prob_hypergeom(total: int, bad: int, samples: int) -> float:
    """
    Probability that **all** samples avoid bad leaves when sampling WITHOUT
    replacement (hypergeometric).

    P_miss = C(total - bad, samples) / C(total, samples)

    If samples > total - bad, the numerator is 0 → P_miss = 0.
    """
    if samples < 0:
        raise ValueError("samples must be non-negative")
    if total < 0 or bad < 0 or bad > total:
        raise ValueError("invalid total/bad")
    if samples == 0:
        return 1.0
    num = _comb(total - bad, samples)
    den = _comb(total, samples)
    if den == 0:
        # total == 0 (degenerate); treat as full miss certainty.
        return 1.0
    return num / den


def miss_prob_binomial(bad_fraction: float, samples: int) -> float:
    """
    Binomial (with-replacement) miss probability:
      P_miss ≈ (1 - f)^s
    """
    if samples < 0:
        raise ValueError("samples must be non-negative")
    if bad_fraction < 0.0 or bad_fraction > 1.0:
        raise ValueError("bad_fraction must be in [0,1]")
    if samples == 0:
        return 1.0
    if bad_fraction == 0.0:
        return 1.0
    return float((1.0 - bad_fraction) ** samples)


def samples_for_p_fail_binomial(bad_fraction: float, target_p_fail: float) -> int:
    """
    Closed-form sample count for binomial model:
      s ≥ log(p_fail) / log(1 - f)
    """
    if not (0.0 < target_p_fail < 1.0):
        raise ValueError("target_p_fail must be in (0,1)")
    if bad_fraction <= 0.0:
        return 0  # nothing to detect
    # Avoid domain errors when f is extremely close to 1
    denom = math.log(max(1e-18, 1.0 - bad_fraction))
    s = math.log(target_p_fail) / denom
    return int(math.ceil(s))


def samples_for_p_fail_hypergeom(total: int, bad: int, target_p_fail: float) -> int:
    """
    Small integer search for the minimal s s.t. miss_prob_hypergeom(total, bad, s) ≤ target.
    Efficient for typical matrix sizes (T up to millions); this is used rarely (sizing/CI).
    """
    if not (0.0 < target_p_fail < 1.0):
        raise ValueError("target_p_fail must be in (0,1)")
    if bad <= 0:
        return 0
    # Coarse lower bound from binomial to speed up
    f = bad / total
    s = samples_for_p_fail_binomial(f, target_p_fail)
    # Tighten upwards until hypergeom bound is met
    while miss_prob_hypergeom(total, bad, s) > target_p_fail:
        s += 1
    return s


# --------------------------------------------------------------------------- #
# Adversary models (minimal withholding to break stripes)
# --------------------------------------------------------------------------- #

def min_withheld_per_broken_stripe(params: ErasureParams) -> int:
    """
    Minimal number of leaves to withhold **in a single stripe** to make it
    unrecoverable: need strictly fewer than k available ⇒ withhold n - k + 1.
    """
    n = params.total_shards
    k = params.data_shards
    return (n - k + 1)


def worst_case_bad_leaves(
    *,
    params: ErasureParams,
    stripes: int,
    stripes_broken: int = 1,
) -> int:
    """
    Adversary concentrates withholding into `stripes_broken` stripes, each with
    the minimal (n - k + 1) leaves removed. This minimizes the global bad
    fraction while still breaking availability.

    Returns the total number of bad leaves B.
    """
    if stripes_broken <= 0:
        raise ValueError("stripes_broken must be ≥ 1")
    if stripes <= 0:
        raise ValueError("stripes must be ≥ 1")
    stripes_broken = min(stripes_broken, stripes)
    return stripes_broken * min_withheld_per_broken_stripe(params)


def bad_fraction_for_broken_stripes(
    *,
    params: ErasureParams,
    stripes: int,
    stripes_broken: int = 1,
) -> float:
    """
    Global fraction of bad leaves under the worst-case minimal-withholding model.
    """
    B = worst_case_bad_leaves(params=params, stripes=stripes, stripes_broken=stripes_broken)
    T = stripes * params.total_shards
    return 0.0 if T == 0 else (B / T)


# --------------------------------------------------------------------------- #
# Sizing helpers: given target p_fail, compute samples
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SamplePlan:
    """
    Result of a sample sizing computation.
    """
    samples: int
    p_fail_model: str  # "binomial" or "hypergeom"
    bad_leaves: int
    total_leaves: int
    bad_fraction: float
    stripes_broken: int
    note: Optional[str] = None


def plan_samples_for_target(
    *,
    params: ErasureParams,
    stripes: int,
    target_p_fail: float,
    stripes_broken: int = 1,
    with_replacement: bool = True,
) -> SamplePlan:
    """
    Compute the number of random samples `s` needed so that the probability of
    **not** touching any withheld leaf is ≤ target_p_fail, under a conservative
    adversary that minimally breaks `stripes_broken` stripes.

    Args:
      params: ErasureParams(k, n, share_bytes, …)
      stripes: number of stripes in the encoded blob (rows)
      target_p_fail: acceptable miss probability in (0,1), e.g., 1e-9
      stripes_broken: how many stripes the adversary breaks (default 1)
      with_replacement: if True (default), use binomial closed form; otherwise
                        search using the exact hypergeometric.

    Returns:
      SamplePlan with fields populated.

    Notes:
      • For large matrices the binomial is an excellent approximation and
        yields a closed form. For small T, hypergeometric can be used.
      • If B == 0 (k == n+1 impossible, or stripes == 0), samples = 0.
    """
    if not (0.0 < target_p_fail < 1.0):
        raise ValueError("target_p_fail must be in (0,1)")

    T = stripes * params.total_shards
    B = worst_case_bad_leaves(params=params, stripes=stripes, stripes_broken=stripes_broken)
    if T <= 0 or B <= 0:
        return SamplePlan(
            samples=0,
            p_fail_model="binomial" if with_replacement else "hypergeom",
            bad_leaves=max(B, 0),
            total_leaves=max(T, 0),
            bad_fraction=0.0 if T <= 0 else (B / T),
            stripes_broken=stripes_broken,
            note="No detectable withholding under given parameters.",
        )

    f = B / T
    if with_replacement:
        s = samples_for_p_fail_binomial(f, target_p_fail)
        model = "binomial"
    else:
        s = samples_for_p_fail_hypergeom(T, B, target_p_fail)
        model = "hypergeom"

    return SamplePlan(
        samples=s,
        p_fail_model=model,
        bad_leaves=B,
        total_leaves=T,
        bad_fraction=f,
        stripes_broken=stripes_broken,
        note=None,
    )


# --------------------------------------------------------------------------- #
# Convenience: compute p_fail for a given s under a worst-case model
# --------------------------------------------------------------------------- #

def p_fail_for_samples(
    *,
    params: ErasureParams,
    stripes: int,
    samples: int,
    stripes_broken: int = 1,
    with_replacement: bool = True,
) -> float:
    """
    Probability of **missing** all withheld leaves when drawing `samples`
    uniformly at random across the matrix, under the minimal-withholding
    model that breaks `stripes_broken` stripes.
    """
    if samples < 0:
        raise ValueError("samples must be non-negative")
    T = stripes * params.total_shards
    B = worst_case_bad_leaves(params=params, stripes=stripes, stripes_broken=stripes_broken)
    if T <= 0 or B <= 0:
        return 1.0  # nothing to detect ⇒ vacuous miss
    if with_replacement:
        return miss_prob_binomial(B / T, samples)
    else:
        return miss_prob_hypergeom(T, B, samples)


# --------------------------------------------------------------------------- #
# Minimal doctest-like smoke
# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover
    # Quick sanity check
    ep = ErasureParams(data_shards=8, total_shards=16, share_bytes=4096)
    stripes = 256
    target = 1e-9
    plan = plan_samples_for_target(params=ep, stripes=stripes, target_p_fail=target, stripes_broken=1)
    print("Plan:", plan)
    print("p_fail at s:", p_fail_for_samples(params=ep, stripes=stripes, samples=plan.samples))
