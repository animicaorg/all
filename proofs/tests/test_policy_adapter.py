from __future__ import annotations

import math
import types
from typing import Any, Callable

import pytest

from proofs.metrics import ProofMetrics
import proofs.policy_adapter as adapter_mod


def _get_adapter() -> Callable[..., float]:
    """
    Be tolerant to the exact function name used in policy_adapter.py.
    We try a few conventional names and return the first match.
    """
    for name in (
        "map_to_psi_inputs",
        "metrics_to_psi_inputs",
        "psi_input_from_metrics",
        "to_psi_input",
    ):
        fn = getattr(adapter_mod, name, None)
        if callable(fn):
            return fn  # type: ignore[return-value]
    raise AssertionError(
        "Could not find an adapter function in proofs.policy_adapter "
        "(tried map_to_psi_inputs / metrics_to_psi_inputs / psi_input_from_metrics / to_psi_input)."
    )


ADAPT = _get_adapter()


def _try_call(kind: Any, m: ProofMetrics) -> float:
    """
    Call the adapter with either a string kind ('hash','ai','vdf',...) or,
    if the adapter rejects that, try common numeric ids (0..).
    """
    # First attempt: pass through kind as-is
    try:
        out = ADAPT(kind, m)  # type: ignore[misc]
        assert isinstance(out, (int, float))
        return float(out)
    except Exception:
        pass

    # Fallback map of "well-known" numeric ids; if your adapter expects ints only,
    # update this table to match proofs/registry.py or consensus/types.py.
    fallback_ids = {
        "hash": 0,
        "ai": 1,
        "quantum": 2,
        "storage": 3,
        "vdf": 4,
    }
    if isinstance(kind, str) and kind in fallback_ids:
        return float(ADAPT(fallback_ids[kind], m))  # type: ignore[misc]

    # Last-ditch: just try a few integers
    for guess in (0, 1, 2, 3, 4):
        try:
            return float(ADAPT(guess, m))  # type: ignore[misc]
        except Exception:
            continue

    raise AssertionError("Adapter invocation failed for kind=%r" % (kind,))


# ------------------------------ HashShare mapping ------------------------------------

def test_hashshare_monotonic_in_d_ratio():
    m_low = ProofMetrics(d_ratio=0.10)
    m_mid = ProofMetrics(d_ratio=0.50)
    m_high = ProofMetrics(d_ratio=0.90)

    psi_low = _try_call("hash", m_low)
    psi_mid = _try_call("hash", m_mid)
    psi_high = _try_call("hash", m_high)

    assert 0.0 <= psi_low < psi_mid < psi_high, "ψ must increase with share difficulty ratio"


# ------------------------------ AI mapping -------------------------------------------

def test_ai_increases_with_units_and_quality():
    # Base: reasonable traps ratio and QoS
    base = ProofMetrics(ai_units=10.0, traps_ratio=0.90, qos=0.95, redundancy=2)

    # More units → higher ψ
    more_units = ProofMetrics(ai_units=20.0, traps_ratio=0.90, qos=0.95, redundancy=2)
    psi_base = _try_call("ai", base)
    psi_more = _try_call("ai", more_units)
    assert psi_more > psi_base >= 0.0

    # Worse traps/QoS → lower ψ (holding units constant)
    worse_quality = ProofMetrics(ai_units=10.0, traps_ratio=0.60, qos=0.80, redundancy=2)
    psi_worse = _try_call("ai", worse_quality)
    assert psi_worse < psi_base

    # Redundancy shouldn't *increase* ψ beyond diminishing returns; ensure it's bounded
    high_redund = ProofMetrics(ai_units=10.0, traps_ratio=0.90, qos=0.95, redundancy=6)
    psi_high_redund = _try_call("ai", high_redund)
    assert psi_high_redund <= psi_more + 1e-9  # at most comparable to more units, not unbounded


def test_ai_zero_units_yields_zero_or_near_zero():
    zero = ProofMetrics(ai_units=0.0, traps_ratio=1.0, qos=1.0, redundancy=1)
    psi = _try_call("ai", zero)
    assert psi >= 0.0
    # Implementations may add tiny epsilons; accept very small value but not significant weight
    assert psi <= 1e-9


# ------------------------------ VDF mapping ------------------------------------------

def test_vdf_seconds_monotonic():
    fast = ProofMetrics(vdf_seconds=0.10)
    slow = ProofMetrics(vdf_seconds=0.50)
    psi_fast = _try_call("vdf", fast)
    psi_slow = _try_call("vdf", slow)
    assert psi_fast >= 0.0
    assert psi_slow > psi_fast, "ψ must increase with vdf_seconds"


# ------------------------------ Non-negativity & stability ---------------------------

@pytest.mark.parametrize(
    "kind,metrics",
    [
        ("hash", ProofMetrics(d_ratio=0.0)),
        ("ai", ProofMetrics(ai_units=0.0, traps_ratio=0.0, qos=0.0, redundancy=0)),
        ("vdf", ProofMetrics(vdf_seconds=0.0)),
    ],
)
def test_non_negative(kind: str, metrics: ProofMetrics):
    psi = _try_call(kind, metrics)
    assert psi >= 0.0


def test_adapter_is_pure_function():
    """
    The mapping should be deterministic & side-effect free: same inputs → same ψ.
    """
    m = ProofMetrics(ai_units=3.14, traps_ratio=0.87, qos=0.92, redundancy=2)
    a = _try_call("ai", m)
    b = _try_call("ai", ProofMetrics(**m.__dict__))  # copy
    assert math.isclose(a, b, rel_tol=0, abs_tol=1e-12)
