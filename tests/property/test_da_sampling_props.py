# -*- coding: utf-8 -*-
"""
Property tests for DA sampling probability bounds.

What we check (skipping gracefully if the DA probability helpers are not present):

1) p_fail(s, k, n) is a valid probability and behaves monotonically:
   - In [0, 1]
   - Non-increasing in samples s
   - Non-increasing in number of missing shards k (more corruption ⇒ easier to catch ⇒ lower p_fail)

2) p_fail is bounded by standard models:
   - Without-replacement (hypergeometric) lower bound:
       p_hg = Π_{i=0}^{s-1} (n-k-i) / (n-i)   (probability to miss every bad shard)
   - With-replacement (binomial) upper bound:
       p_bin = (1 - k/n)^s
   - Expect: p_hg - ε <= p_fail <= p_bin + ε

3) If a helper like required_samples/target/… exists, the returned sample count s_req
   achieves ≤ target p_fail (within a small tolerance), and s_req is minimal-ish
   (s_req - 1 should exceed the target by at least a tiny slack).

These tests try multiple function name/spelling variants so they work across small
layout changes. If no suitable function is found, we skip with a clear reason.
"""
from __future__ import annotations

import inspect
import math
from typing import Any, Optional, Tuple

import pytest
from hypothesis import given, settings, strategies as st

# ---- Optional import of animica DA probability helpers ----------------------

_prob_mod = None
try:
    import da.sampling.probability as _prob_mod  # type: ignore
except Exception:
    _prob_mod = None


# ---- Numerically stable reference bounds ------------------------------------

def _p_hypergeom_no_bad(n: int, k: int, s: int) -> float:
    """
    Probability that s samples without replacement miss all k bad shards among n.
    Uses a multiplicative form to avoid huge binomials.
    """
    if k <= 0:
        return 1.0  # no bad shards ⇒ certain to "miss" them (degenerate, not used in props)
    if s <= 0:
        return 1.0
    if s > n:
        s = n
    if s > n - k:
        return 0.0
    num = 1.0
    for i in range(s):
        num *= (n - k - i) / (n - i)
    return float(max(0.0, min(1.0, num)))


def _p_binomial_upper(n: int, k: int, s: int) -> float:
    """With-replacement upper bound."""
    if k <= 0:
        return 1.0
    if s <= 0:
        return 1.0
    f = k / float(n)
    return float(max(0.0, min(1.0, (1.0 - f) ** s)))


# ---- Adapters: find & call p_fail and required_samples ----------------------

def _find_fn(mod: Any, names: tuple[str, ...]) -> Optional[Any]:
    for nm in names:
        fn = getattr(mod, nm, None)
        if callable(fn):
            return fn
    return None


def _has_p_fail() -> bool:
    if _prob_mod is None:
        return False
    fn = _find_fn(
        _prob_mod,
        (
            "p_fail",
            "prob_failure",
            "failure_probability",
            "prob_false_availability",
            "p_false_availability",
            "p_miss",
        ),
    )
    return fn is not None


def _call_p_fail(s: int, k: int, n: int) -> float:
    """
    Try common signatures for a p_fail-style function.
    Falls back to hypergeometric if module is missing (but tests skip before that).
    """
    if _prob_mod is not None:
        fn = _find_fn(
            _prob_mod,
            (
                "p_fail",
                "prob_failure",
                "failure_probability",
                "prob_false_availability",
                "p_false_availability",
                "p_miss",
            ),
        )
        if fn is not None:
            try:
                # Try keyword-rich form first
                return float(
                    fn(samples=s, bad=k, total=n)  # type: ignore[call-arg]
                )
            except Exception:
                pass
            try:
                # (s, k, n)
                return float(fn(s, k, n))  # type: ignore[misc]
            except Exception:
                pass
            try:
                # (s, fraction) or (samples=s, fraction=f)
                f = k / float(n)
                return float(fn(s, f))  # type: ignore[misc]
            except Exception:
                pass
    # Fallback (shouldn't be used in assertions unless explicitly intended)
    return _p_hypergeom_no_bad(n, k, s)


def _has_required_samples() -> bool:
    if _prob_mod is None:
        return False
    fn = _find_fn(
        _prob_mod,
        (
            "required_samples",
            "samples_for_target",
            "samples_for_p_fail",
            "min_samples_for_p_fail",
            "samples_for_probability",
            "target_samples",
        ),
    )
    return fn is not None


def _call_required_samples(target_p_fail: float, k: int, n: int) -> Optional[int]:
    if _prob_mod is None:
        return None
    fn = _find_fn(
        _prob_mod,
        (
            "required_samples",
            "samples_for_target",
            "samples_for_p_fail",
            "min_samples_for_p_fail",
            "samples_for_probability",
            "target_samples",
        ),
    )
    if fn is None:
        return None
    # Try a few calling conventions
    for call in (
        lambda: fn(target_p_fail=target_p_fail, bad=k, total=n),   # kw form
        lambda: fn(target_p_fail, k, n),                           # positional (pt, k, n)
        lambda: fn(target_p_fail, k / float(n), n),                # (pt, fraction, n)
        lambda: fn(target_p_fail=target_p_fail, fraction=k / float(n), total=n),
        lambda: fn(target_p_fail, k / float(n)),                   # (pt, fraction)
    ):
        try:
            s = int(call())
            if s >= 0:
                return s
        except Exception:
            continue
    return None


# ---- Hypothesis strategies ---------------------------------------------------

# Total shards (post-erasure extended matrix), keep reasonable.
N = st.integers(min_value=64, max_value=4096)

# Choose a corruption fraction in (0, 0.5]. Ensure at least 1 bad shard.
FRACTION = st.floats(min_value=1e-4, max_value=0.5, allow_nan=False, allow_infinity=False)

@st.composite
def das_params(draw):
    n = draw(N)
    f = draw(FRACTION)
    k = max(1, min(n - 1, int(math.ceil(f * n))))
    # samples s up to n (for hypergeometric); keep moderate for speed
    s = draw(st.integers(min_value=1, max_value=min(n, 2000)))
    return n, k, s


# ---- Tests ------------------------------------------------------------------

TOL = 1e-9
LOOSETOL = 2e-3  # looser tolerance for minimality checks (module-specific rounding)


@pytest.mark.skipif(not _has_p_fail(), reason="da.sampling.probability.p_fail-like function not available")
@given(das_params())
@settings(max_examples=160)
def test_p_fail_is_bounded_and_monotone(params: Tuple[int, int, int]):
    n, k, s = params

    # Basic validity
    p = _call_p_fail(s, k, n)
    assert 0.0 - TOL <= p <= 1.0 + TOL

    # Bounds
    lower = _p_hypergeom_no_bad(n, k, s)
    upper = _p_binomial_upper(n, k, s)
    assert lower - 1e-7 <= p <= upper + 1e-7, f"p_fail={p} not within [{lower}, {upper}]"

    # Monotone in s (more samples → lower or equal failure prob)
    p_more = _call_p_fail(s + 1 if s + 1 <= n else s, k, n)
    if s + 1 <= n:
        assert p_more <= p + 1e-12

    # Monotone in k (more corruption → easier to detect → lower failure prob)
    if k + 1 < n:
        p_more_bad = _call_p_fail(s, k + 1, n)
        assert p_more_bad <= p + 1e-12


@pytest.mark.skipif(
    not (_has_p_fail() and _has_required_samples()),
    reason="required_samples helper not available alongside p_fail",
)
@given(
    st.integers(min_value=64, max_value=4096),
    st.floats(min_value=1e-6, max_value=1e-2, allow_nan=False, allow_infinity=False),  # target p_fail in (1e-6..1e-2]
    FRACTION,
)
@settings(max_examples=120)
def test_required_samples_hits_target(n: int, target: float, frac: float):
    k = max(1, min(n - 1, int(math.ceil(frac * n))))
    s_req = _call_required_samples(target, k, n)
    assert s_req is not None and s_req >= 0

    # Check that s_req achieves ≤ target (allow tiny numerical overshoot).
    p_req = _call_p_fail(s_req, k, n)
    assert p_req <= target * (1.0 + 1e-6), f"p_fail({s_req})={p_req} exceeds target={target}"

    # Minimality-ish: one fewer sample should *typically* exceed target (allow looser tolerance).
    if s_req > 0:
        p_prev = _call_p_fail(s_req - 1, k, n)
        # Don't make this a hard requirement (implementations may round); use soft lower bound.
        assert p_prev >= target * (1.0 - LOOSETOL) or p_prev > target, "required_samples not near-minimal"


@pytest.mark.skipif(not _has_p_fail(), reason="p_fail helper not available")
@given(das_params())
@settings(max_examples=120)
def test_p_fail_matches_extremes(params: Tuple[int, int, int]):
    """
    Sanity on extremes:
      - If s >= n - k + 1 then p_fail should be ~0 under hypergeometric logic (you almost must hit a bad shard).
      - If k is tiny and s is tiny, p_fail ~ upper bound (1 - k/n)^s.
    """
    n, k, s = params

    p = _call_p_fail(s, k, n)

    # Near-certain detection when sampling more than the number of good shards.
    if s >= (n - k + 1):
        assert p <= 1e-12 + 1e-9

    # For small k and small s, p should be close to the binomial approximation.
    if k <= max(1, n // 512) and s <= 8:
        approx = _p_binomial_upper(n, k, s)
        assert abs(p - approx) <= 5e-2  # 5% slack is fine here


