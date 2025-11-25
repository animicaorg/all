from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

import math
import pytest

# We deliberately write these tests to be resilient to small API name changes.
# The traps module must expose functions to compute a pass ratio, a confidence
# lower bound (Wilson or equivalent), and optionally a threshold decision and a
# QoS scaler. We dynamically discover them by common names.
from proofs.quantum_attest import traps as traps_mod  # type: ignore[import]


# ----------------------------- helpers ---------------------------------

def _get_fn(mod: Any, *names: str) -> Optional[Callable[..., Any]]:
    for n in names:
        fn = getattr(mod, n, None)
        if callable(fn):
            return fn
    return None


ratio_fn = _get_fn(
    traps_mod,
    "trap_ratio",
    "calc_trap_ratio",
    "compute_trap_ratio",
    "ratio",
    "pass_ratio",
)
lcb_fn = _get_fn(
    traps_mod,
    "wilson_lower_bound",
    "wilson_lcb",
    "lower_conf_bound",
    "lower_confidence_bound",
    "ci_lower",
)
decide_fn = _get_fn(
    traps_mod,
    "meets_threshold",
    "accept",
    "accept_traps",
    "passes_threshold",
    "decision",
)
qos_fn = _get_fn(
    traps_mod,
    "qos_from_traps",
    "qos_score",
    "scale_qos",
    "qos",
)

assert ratio_fn is not None, "traps module must expose a trap pass-ratio function"
assert lcb_fn is not None, "traps module must expose a Wilson (or equivalent) lower bound function"


def _call_decide(passes: int, total: int, threshold: float = 0.9) -> Optional[bool]:
    """
    Try a few common calling conventions for the decision function.
    Returns None if no decision function is available.
    """
    if decide_fn is None:
        return None
    try:
        return bool(decide_fn(passes, total, threshold=threshold))  # type: ignore[misc]
    except TypeError:
        pass
    try:
        return bool(decide_fn(passes, total, threshold))  # type: ignore[misc]
    except TypeError:
        pass
    try:
        return bool(decide_fn(passes=passes, total=total, t=threshold))  # type: ignore[misc]
    except TypeError:
        pass
    # Last resort: call and coerce truthiness
    return bool(decide_fn(passes, total))  # type: ignore[misc]


# ----------------------------- tests -----------------------------------

@pytest.mark.parametrize("total", [20, 100, 1000])
def test_wilson_lcb_monotonic_in_passes(total: int):
    """
    The lower confidence bound must be non-decreasing in the number of passes,
    and stay within [0,1].
    """
    l1 = float(lcb_fn(0, total))  # type: ignore[misc]
    l2 = float(lcb_fn(total // 4, total))  # type: ignore[misc]
    l3 = float(lcb_fn(total // 2, total))  # type: ignore[misc]
    l4 = float(lcb_fn(3 * total // 4, total))  # type: ignore[misc]
    l5 = float(lcb_fn(total, total))  # type: ignore[misc]

    assert 0.0 <= l1 <= l2 <= l3 <= l4 <= l5 <= 1.0


@pytest.mark.parametrize("passes,total", [(0, 1), (5, 10), (75, 100), (950, 1000)])
def test_ratio_in_0_1_and_matches_simple_definition(passes: int, total: int):
    r = float(ratio_fn(passes, total))  # type: ignore[misc]
    assert 0.0 <= r <= 1.0
    # Within a tiny epsilon of naive passes/total (implementations may guard division-by-zero).
    expect = 0.0 if total == 0 else passes / total
    assert abs(r - expect) <= 1e-12


def test_lcb_increases_with_sample_size_for_same_observed_ratio():
    """
    For the same observed pass ratio, the lower bound should increase with sample size.
    Example: 90% passes at 100 trials has lower LCB than 90% at 1000 trials.
    """
    p1, t1 = 90, 100
    p2, t2 = 900, 1000
    l1 = float(lcb_fn(p1, t1))  # type: ignore[misc]
    l2 = float(lcb_fn(p2, t2))  # type: ignore[misc]
    assert l2 > l1, (l1, l2)


def test_threshold_decision_behaves_sensibly():
    """
    If a decision function exists, it should accept clearly-good cases and reject clearly-bad ones
    at a reasonable threshold (e.g., 0.9).
    """
    decision = _call_decide(950, 1000, threshold=0.9)
    if decision is None:
        pytest.skip("No decision function exported; ratio/LCB tests already cover correctness")
    assert decision is True

    decision = _call_decide(850, 1000, threshold=0.9)
    assert decision is False


@pytest.mark.parametrize(
    "passes,total,thr,expect",
    [
        (96, 100, 0.90, True),
        (91, 100, 0.95, False),  # borderline: 91% typically not enough for 95% LCB
        (980, 1000, 0.95, True),
        (880, 1000, 0.95, False),
    ],
)
def test_threshold_matrix(passes: int, total: int, thr: float, expect: bool):
    """
    Matrix around common thresholds to catch regressions in the decision boundary.
    """
    decision = _call_decide(passes, total, threshold=thr)
    if decision is None:
        pytest.skip("No decision function exported; ratio/LCB tests already cover correctness")
    # We allow implementations to be slightly conservative; only assert on clear cases.
    # The above pairs are chosen to be far enough from the boundary for Wilson LCB with z≈1.96–3.0.
    assert decision is expect


# ----------------------------- QoS scaling ------------------------------

@pytest.mark.skipif(qos_fn is None, reason="No QoS scaler exported by traps module")
def test_qos_monotonic_in_ratio_and_redundancy_and_inverse_in_latency():
    """
    QoS should:
      - increase as trap ratio increases (holding others fixed),
      - increase with redundancy (more independent trap circuits),
      - decrease as latency grows beyond target.
    """
    # Baseline
    q_baseline = float(qos_fn(trap_ratio=0.90, redundancy=1, latency_s=2.0, target_latency_s=2.0))  # type: ignore[misc]

    # Better ratio → strictly higher QoS
    q_ratio_up = float(qos_fn(trap_ratio=0.98, redundancy=1, latency_s=2.0, target_latency_s=2.0))  # type: ignore[misc]
    assert q_ratio_up > q_baseline

    # More redundancy → higher QoS (diminishing returns acceptable)
    q_red_up = float(qos_fn(trap_ratio=0.90, redundancy=3, latency_s=2.0, target_latency_s=2.0))  # type: ignore[misc]
    assert q_red_up > q_baseline

    # Worse latency than target → lower QoS
    q_latency_down = float(qos_fn(trap_ratio=0.90, redundancy=1, latency_s=5.0, target_latency_s=2.0))  # type: ignore[misc]
    assert q_latency_down < q_baseline

    # Very good everything should clamp to ≤1.0
    q_ceiling = float(qos_fn(trap_ratio=0.999, redundancy=8, latency_s=0.2, target_latency_s=2.0))  # type: ignore[misc]
    assert 0.0 <= q_ceiling <= 1.0


@pytest.mark.skipif(qos_fn is None, reason="No QoS scaler exported by traps module")
def test_qos_continuity_near_threshold():
    """
    QoS should not jump sharply for tiny changes around the acceptance threshold.
    This guards against step functions that create incentive cliffs.
    """
    thr = 0.95
    # Pick two ratios very close around the threshold.
    q_low = float(qos_fn(trap_ratio=thr - 0.001, redundancy=2, latency_s=1.0, target_latency_s=2.0))  # type: ignore[misc]
    q_high = float(qos_fn(trap_ratio=thr + 0.001, redundancy=2, latency_s=1.0, target_latency_s=2.0))  # type: ignore[misc]
    # The delta should be small (no cliff); adapt bound if implementation uses a softplus/sigmoid.
    assert abs(q_high - q_low) < 0.15, (q_low, q_high)
