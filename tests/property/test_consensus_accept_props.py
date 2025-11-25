# -*- coding: utf-8 -*-
"""
Property tests for PoIES acceptance probability around Θ.

Acceptance rule (per spec): accept iff S = H(u) + Σψ >= Θ,
where u ~ Uniform(0,1], H(u) = -ln(u).

We check distributional properties using Monte Carlo:
  1) For fixed Θ and Δ := Θ - Σψ >= 0, accept probability ≈ exp(-Δ).
  2) Monotonicity:
     - Increasing Σψ (holding Θ) increases (or keeps) acceptance probability.
     - Increasing Θ (holding Σψ) decreases (or keeps) acceptance probability.
  3) Edge sanity:
     - If Σψ >= Θ then acceptance is ~1 (since H(u) >= 0 always).
     - If Θ - Σψ is large, acceptance is very small.

The tests try to use consensus.scorer's acceptance predicate if present.
If a compatible function cannot be found, tests are skipped (we do NOT
reimplement consensus here — we want to exercise the project implementation).
"""
from __future__ import annotations

import inspect
import math
import random
from typing import Any, Callable, Optional, Tuple

import pytest
from hypothesis import given, settings, strategies as st


# -----------------------------------------------------------------------------
# Locate an acceptance predicate in consensus.scorer (best-effort)
# -----------------------------------------------------------------------------

_scorer_mod = None
try:
    import consensus.scorer as _scorer_mod  # type: ignore
except Exception:
    _scorer_mod = None


def _find_accept_fn(mod: Any) -> Optional[Callable[..., bool]]:
    if mod is None:
        return None
    # Candidate function names people commonly use
    names = (
        "accept",
        "accepts",
        "should_accept",
        "accept_predicate",
        "decide",
        "decision",
        "predicate",
    )
    for nm in names:
        fn = getattr(mod, nm, None)
        if callable(fn):
            return fn
    # Fall back to a class with an accept-like method (if zero-arg ctor works)
    for clsname in ("Scorer", "PoIESScorer", "Aggregator", "PoIESAggregator"):
        cls = getattr(mod, clsname, None)
        if cls is None:
            continue
        try:
            inst = cls()  # type: ignore[call-arg]
        except Exception:
            continue
        for meth in ("accept", "accepts", "decide", "should_accept"):
            if hasattr(inst, meth) and callable(getattr(inst, meth)):
                fn_obj = getattr(inst, meth)
                # Bind instance method into a plain callable
                return lambda psi_sum, theta, u=None, _m=meth, _inst=inst: getattr(_inst, _m)(psi_sum, theta, u)  # type: ignore[misc]
    return None


_ACCEPT_FN = _find_accept_fn(_scorer_mod)


def _has_accept() -> bool:
    return _ACCEPT_FN is not None


def _call_accept(psi_sum: float, theta: float, u: Optional[float]) -> bool:
    """
    Call the discovered accept predicate with flexible signatures.
    Prefer passing `u` if supported; otherwise rely on internal RNG.
    """
    fn = _ACCEPT_FN
    assert fn is not None, "accept predicate not available"

    # Try keyword-rich form first
    try:
        if u is None:
            return bool(fn(psi_sum=psi_sum, theta=theta))  # type: ignore[call-arg]
        return bool(fn(psi_sum=psi_sum, theta=theta, u=u))  # type: ignore[call-arg]
    except Exception:
        pass

    # Inspect if 'u' is accepted
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        takes_u = any(p for p in params.values() if p.name in ("u", "uniform", "rand_u"))
    except Exception:
        takes_u = False

    # Positional invocations
    try:
        if takes_u and u is not None:
            return bool(fn(psi_sum, theta, u))  # type: ignore[misc]
        return bool(fn(psi_sum, theta))  # type: ignore[misc]
    except Exception:
        # Last attempt: maybe order is (theta, psi, u)
        try:
            if takes_u and u is not None:
                return bool(fn(theta, psi_sum, u))  # type: ignore[misc]
            return bool(fn(theta, psi_sum))  # type: ignore[misc]
        except Exception as exc:
            raise AssertionError(f"Could not invoke accept predicate: {exc}") from exc


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _expected_prob(theta: float, psi_sum: float) -> float:
    """
    P[H(u) + Σψ >= Θ] with H(u) = -ln u, u~U(0,1]:
      Let Δ = Θ - Σψ. If Δ <= 0 ⇒ 1. Else ⇒ e^{-Δ}.
    """
    delta = theta - psi_sum
    if delta <= 0.0:
        return 1.0
    return math.exp(-float(delta))


def _mc_rate(theta: float, psi_sum: float, trials: int = 800, seed: int = 42) -> float:
    rng = random.Random(seed)
    acc = 0
    for _ in range(trials):
        # Draw u in (0,1]; avoid 0
        u = max(rng.random(), 1e-12)
        acc += 1 if _call_accept(psi_sum, theta, u) else 0
    return acc / float(trials)


# Hypothesis strategies (Θ and ψ in "nats" domain, moderate ranges)
THETA = st.floats(min_value=0.1, max_value=12.0, allow_nan=False, allow_infinity=False)
DELTA = st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False)  # Θ - Σψ
PSI = st.floats(min_value=-6.0, max_value=10.0, allow_nan=False, allow_infinity=False)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

@pytest.mark.skipif(not _has_accept(), reason="consensus.scorer acceptance predicate not available")
@given(THETA, DELTA)
@settings(max_examples=60)
def test_accept_probability_matches_theory(theta: float, delta: float):
    """
    For Δ = Θ - Σψ, acceptance probability ≈ e^{-Δ} (or 1 if Δ<=0).
    """
    psi_sum = theta - delta
    p_exp = _expected_prob(theta, psi_sum)
    p_emp = _mc_rate(theta, psi_sum, trials=1000, seed=1337)

    # Binomial SE worst at p=0.5 → ~1.6% for N=1000; give generous margin.
    assert abs(p_emp - p_exp) <= 0.07, f"empirical={p_emp:.4f} vs expected={p_exp:.4f} (Θ={theta:.3f}, ψ={psi_sum:.3f})"


@pytest.mark.skipif(not _has_accept(), reason="consensus.scorer acceptance predicate not available")
@given(THETA, st.floats(min_value=-4.0, max_value=4.0), st.floats(min_value=0.1, max_value=2.5))
@settings(max_examples=70)
def test_monotone_in_psi(theta: float, psi_base: float, bump: float):
    """
    Increasing Σψ (with Θ fixed) should not decrease acceptance probability.
    """
    psi_low = psi_base
    psi_high = psi_base + abs(bump)

    p_low = _mc_rate(theta, psi_low, trials=700, seed=7)
    p_high = _mc_rate(theta, psi_high, trials=700, seed=8)

    # Allow small noise but enforce the trend.
    assert p_high + 0.02 >= p_low, f"p_high={p_high:.3f} < p_low={p_low:.3f} (Θ={theta:.3f})"


@pytest.mark.skipif(not _has_accept(), reason="consensus.scorer acceptance predicate not available")
@given(PSI, st.floats(min_value=0.5, max_value=10.0), st.floats(min_value=0.1, max_value=2.5))
@settings(max_examples=70)
def test_monotone_in_theta(psi_sum: float, theta_base: float, bump: float):
    """
    Increasing Θ (with Σψ fixed) should not increase acceptance probability.
    """
    theta_lo = theta_base
    theta_hi = theta_base + abs(bump)

    p_lo = _mc_rate(theta_lo, psi_sum, trials=700, seed=9)
    p_hi = _mc_rate(theta_hi, psi_sum, trials=700, seed=10)

    assert p_lo + 0.02 >= p_hi, f"p_hi={p_hi:.3f} > p_lo={p_lo:.3f} (ψ={psi_sum:.3f})"


@pytest.mark.skipif(not _has_accept(), reason="consensus.scorer acceptance predicate not available")
@given(THETA, st.floats(min_value=0.0, max_value=3.0))
@settings(max_examples=40)
def test_accept_always_when_psi_ge_theta(theta: float, extra: float):
    """
    If Σψ >= Θ then acceptance should be ~1 (H(u) >= 0).
    """
    psi_sum = theta + abs(extra) + 1e-9
    # A handful of trials should all accept.
    rng = random.Random(123)
    results = []
    for _ in range(64):
        u = max(rng.random(), 1e-12)
        results.append(_call_accept(psi_sum, theta, u))
    assert all(results), f"Found rejection despite ψ>=Θ (Θ={theta:.4f}, ψ={psi_sum:.4f})"


@pytest.mark.skipif(not _has_accept(), reason="consensus.scorer acceptance predicate not available")
@given(THETA, st.floats(min_value=6.0, max_value=10.0))
@settings(max_examples=40)
def test_accept_rare_when_theta_far_above_psi(theta: float, delta_big: float):
    """
    If Θ - Σψ is large, acceptance probability should be tiny.
    """
    psi_sum = theta - delta_big
    p_emp = _mc_rate(theta, psi_sum, trials=1500, seed=202)
    # e^{-6} ≈ 0.0025; e^{-10} ≈ 4.5e-5 → use a conservative ceiling
    assert p_emp <= 0.01, f"Acceptance too high for large Δ (emp={p_emp:.4f}, Δ={delta_big:.3f})"


