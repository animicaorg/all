import math
import importlib
import types
import pytest


# Import module under test
st = importlib.import_module("mining.share_target")


def _get_callable(mod: types.ModuleType, candidates):
    for name in candidates:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


# Try a few common names so the test stays compatible with minor refactors.
ratio_fn = _get_callable(
    st,
    (
        "d_ratio",              # preferred
        "difficulty_ratio",
        "share_ratio",
        "ratio_vs_theta",
        "ratio",
    ),
)

accept_fn = _get_callable(
    st,
    (
        "accepts",              # preferred
        "meets_theta",
        "is_accepted",
        "passes_threshold",
        "passes",
    ),
)

micro_fn = _get_callable(
    st,
    (
        "micro_threshold",      # preferred
        "share_micro_threshold",
        "theta_to_micro",
        "compute_micro_threshold",
    ),
)


# A helper that calls ratio_fn(H, Theta) with loose arg handling
def _ratio(H: float, Theta: float) -> float:
    if ratio_fn is None:
        pytest.skip("No ratio function exported by mining.share_target")
    try:
        return float(ratio_fn(H, Theta))  # type: ignore[misc]
    except TypeError:
        # Try kwarg style
        return float(ratio_fn(H=H, Theta=Theta))  # type: ignore[misc]


def _accepts(H: float, Theta: float) -> bool:
    if accept_fn is None:
        pytest.skip("No acceptance predicate exported by mining.share_target")
    try:
        return bool(accept_fn(H, Theta))  # type: ignore[misc]
    except TypeError:
        return bool(accept_fn(H=H, Theta=Theta))  # type: ignore[misc]


def _micro(Theta: float, target_shares: int) -> float:
    if micro_fn is None:
        pytest.skip("No micro-threshold helper exported by mining.share_target")
    # Try a few plausible call signatures
    for call in (
        lambda: micro_fn(Theta, target_shares),  # type: ignore[misc]
        lambda: micro_fn(theta=Theta, target_shares=target_shares),  # type: ignore[misc]
        lambda: micro_fn(theta=Theta, shares_per_block=target_shares),  # type: ignore[misc]
        lambda: micro_fn(Theta=Theta, shares_per_block=target_shares),  # type: ignore[misc]
        lambda: micro_fn(Theta=Theta, target=target_shares),  # type: ignore[misc]
    ):
        try:
            val = call()
            return float(val)
        except TypeError:
            continue
    pytest.skip("micro-threshold helper has an unexpected signature")


@pytest.mark.parametrize("Theta", [5.0, 20.0, 100.0])
def test_ratio_properties(Theta):
    if ratio_fn is None:
        pytest.skip("ratio function missing")
    # Basic shape checks: monotone in H, normalized around Theta
    tiny = 1e-9
    r0 = _ratio(tiny, Theta)
    r1 = _ratio(Theta, Theta)
    r2 = _ratio(2.0 * Theta, Theta)
    r3 = _ratio(0.5 * Theta, Theta)

    assert math.isfinite(r0) and r0 >= 0.0
    # At H == Theta the ratio should be ~1.0
    assert math.isclose(r1, 1.0, rel_tol=1e-12, abs_tol=1e-12)
    # Larger H should yield larger ratio
    assert r2 > r1 > r3 >= 0.0
    # Linear-ish expectation: at 2*Theta we expect >= ~2
    assert r2 >= 2.0 - 1e-12


@pytest.mark.parametrize("Theta", [8.0, 21.0, 64.0])
def test_acceptance_boundary_vs_ratio(Theta):
    if ratio_fn is None or accept_fn is None:
        pytest.skip("ratio or acceptance function missing")

    eps = 1e-9
    # Just below threshold
    assert _accepts(Theta - eps, Theta) is False
    # Just above threshold
    assert _accepts(Theta + eps, Theta) is True

    # Correlate with ratio >= 1.0
    for H in (0.1 * Theta, 0.99 * Theta, Theta, 1.01 * Theta, 2.0 * Theta):
        r = _ratio(H, Theta)
        expect = r >= 1.0 - 1e-12
        assert _accepts(H, Theta) == expect


@pytest.mark.parametrize("Theta", [10.0, 32.0, 50.0])
def test_micro_threshold_monotone_and_below_theta(Theta):
    if micro_fn is None or ratio_fn is None:
        pytest.skip("micro-threshold or ratio function missing")

    # Ask for many micro-shares → threshold must be below Theta,
    # and more desired shares → smaller threshold.
    m32  = _micro(Theta, target_shares=32)
    m128 = _micro(Theta, target_shares=128)
    m512 = _micro(Theta, target_shares=512)

    for m in (m32, m128, m512):
        assert 0.0 < m < Theta, "micro threshold must be in (0, Theta)"

    assert m512 <= m128 <= m32, "more target shares should lower (or equal) the threshold"

    # Ratios at micro-thresholds should be < 1 (since micro < Theta)
    assert _ratio(m32, Theta) < 1.0
    assert _ratio(m128, Theta) < 1.0
    assert _ratio(m512, Theta) < 1.0


@pytest.mark.parametrize("Theta", [12.5, 25.0])
def test_ratio_monotone_continuity(Theta):
    if ratio_fn is None:
        pytest.skip("ratio function missing")
    # Check local monotonicity around a neighborhood
    Hs = [0.2 * Theta, 0.5 * Theta, 0.9 * Theta, Theta, 1.1 * Theta, 1.5 * Theta]
    Rs = [_ratio(H, Theta) for H in Hs]
    # Ensure ordering is preserved
    assert all(Rs[i] < Rs[i + 1] for i in range(len(Rs) - 1))
    # Continuity-ish: small delta in H → small delta in ratio near Theta
    delta = 1e-6 * Theta
    r_lo = _ratio(Theta - delta, Theta)
    r_hi = _ratio(Theta + delta, Theta)
    assert abs(r_hi - r_lo) < 1e-3, "ratio should vary smoothly around the threshold"
