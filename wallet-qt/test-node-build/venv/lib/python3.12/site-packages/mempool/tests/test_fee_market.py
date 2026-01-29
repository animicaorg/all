from __future__ import annotations

import math
import types
from typing import Any, Callable, Optional

import pytest

fm = pytest.importorskip(
    "mempool.fee_market", reason="mempool.fee_market module not found"
)


# -------------------------
# Small helpers
# -------------------------


def _get_attr_any(obj: Any, names: list[str]) -> Optional[Callable[..., Any] | Any]:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _ema_series(values: list[float], alpha: float) -> float:
    """Standard EMA with first sample as initial state."""
    assert values, "need at least one value"
    ema = float(values[0])
    for v in values[1:]:
        ema = alpha * float(v) + (1.0 - alpha) * ema
    return ema


def _new_fee_market(alpha: float = 0.2, base_floor: int = 1) -> Any:
    """
    Instantiate whatever the module exposes:
      - FeeMarket(alpha=?, base_floor=?)
      - Market/Estimator(...)
      - else, return a SimpleNamespace with function-based API markers.
    """
    for cls_name in ("FeeMarket", "Market", "FeeEstimator", "DynamicFeeMarket"):
        cls = _get_attr_any(fm, [cls_name])
        if cls:
            try:
                # Try common constructor shapes
                try:
                    return cls(alpha=alpha, base_floor=base_floor)
                except TypeError:
                    return cls(alpha)
            except Exception:
                continue
    return types.SimpleNamespace(functional=True)


def _observe_block_fee(market: Any, fee: int) -> None:
    """
    Feed one block's reference fee into the market (median/paid/base as defined by impl).
    """
    fn = _get_attr_any(
        market,
        [
            "observe_block",
            "update_with_block",
            "apply_block",
            "record_block",
            "add_sample",
        ],
    )
    if callable(fn):
        try:
            fn(fee)  # type: ignore[misc]
            return
        except TypeError:
            # Some APIs accept a richer record; pass minimal dict
            fn({"fee": fee})  # type: ignore[misc]
            return

    # Function-style module?
    fn_mod = _get_attr_any(
        fm,
        [
            "observe_block",
            "update_with_block",
            "apply_block",
            "record_block",
            "add_sample",
        ],
    )
    if callable(fn_mod):
        try:
            fn_mod(fee)  # type: ignore[misc]
        except TypeError:
            fn_mod({"fee": fee})  # type: ignore[misc]


def _current_floor(market: Any) -> Optional[float]:
    val = _get_attr_any(market, ["floor", "min_fee", "dynamic_floor", "current_floor"])
    if isinstance(val, (int, float)):
        return float(val)
    if callable(val):
        try:
            got = val()
            if isinstance(got, (int, float)):
                return float(got)
        except Exception:
            pass
    # Function-style: fm.current_floor()
    fn_mod = _get_attr_any(fm, ["floor", "min_fee", "dynamic_floor", "current_floor"])
    if callable(fn_mod):
        try:
            got = fn_mod()
            if isinstance(got, (int, float)):
                return float(got)
        except Exception:
            pass
    return None


def _compute_floor_from_list(
    values: list[int], alpha: float, base_floor: int
) -> Optional[float]:
    """
    Prefer a single-shot API if available, else emulate by constructing a market and observing.
    """
    fn = _get_attr_any(
        fm,
        [
            "compute_dynamic_floor",
            "compute_floor",
            "dynamic_floor_from_fees",
            "min_fee_from_history",
        ],
    )
    if callable(fn):
        # Try to pass alpha/base_floor if accepted
        try:
            return float(fn(values, alpha=alpha, base_floor=base_floor))  # type: ignore[call-arg]
        except TypeError:
            try:
                return float(fn(values, alpha))  # type: ignore[misc]
            except TypeError:
                return float(fn(values))  # type: ignore[misc]

    # Stateful path
    market = _new_fee_market(alpha=alpha, base_floor=base_floor)
    for v in values:
        _observe_block_fee(market, v)
    return _current_floor(market)


def _surge_multiplier(load: float) -> Optional[float]:
    """
    Ask the module for a surge multiplier given utilization/load.
    """
    fn = _get_attr_any(
        fm,
        [
            "surge_multiplier",
            "compute_surge_multiplier",
            "calc_surge",
            "surge",
            "multiplier_for_load",
        ],
    )
    if callable(fn):
        try:
            return float(fn(load))  # type: ignore[misc]
        except TypeError:
            # Some APIs expect utilization as percent (0-100)
            return float(fn(load * 100.0))  # type: ignore[misc]
    # Maybe provided as a class method/constant curve; skip if absent.
    return None


# -------------------------
# Tests
# -------------------------


def test_floor_ema_tracks_recent_blocks():
    """
    The dynamic floor should approximate an EMA of recent realized fees,
    bounded below by a base floor if applicable.
    """
    alpha = 0.25  # 25% weight to new observations
    base_floor = 100
    samples = [100, 200, 300, 150, 180]

    got = _compute_floor_from_list(samples, alpha=alpha, base_floor=base_floor)
    assert got is not None, "fee_market did not expose a usable floor computation API"

    expect = max(base_floor, _ema_series(samples, alpha))
    # Allow small rounding differences (int vs float, round vs floor)
    assert math.isclose(
        got, expect, rel_tol=0.0, abs_tol=2.0
    ), f"got {got}, expected ~{expect}"


def test_floor_respects_base_minimum():
    """
    If historical fees dip below the base floor, min fee should not drop under it.
    """
    alpha = 0.5
    base_floor = 500
    samples = [50, 60, 70, 80, 90]

    got = _compute_floor_from_list(samples, alpha=alpha, base_floor=base_floor)
    assert got is not None
    assert got >= base_floor - 1, f"floor {got} fell below base {base_floor}"


def test_surge_multiplier_monotone_above_one_when_congested():
    """
    Multiplier should be ~1.0 at/under capacity and >1.0 when load > 1.
    Also check monotonicity: higher load => higher multiplier.
    """
    m1 = _surge_multiplier(1.0)
    m095 = _surge_multiplier(0.95)
    m12 = _surge_multiplier(1.2)
    m20 = _surge_multiplier(2.0)

    if None in (m1, m095, m12, m20):
        pytest.skip("No surge multiplier API exposed by fee_market")

    assert m095 is not None and m095 <= 1.0 + 1e-9
    assert m1 is not None and 0.95 <= m1 <= 1.05, "at capacity multiplier should be ~1"
    assert (
        m12 is not None and m12 > 1.0
    ), "multiplier should increase when load exceeds capacity"
    assert m20 is not None and m20 > m12, "multiplier should be monotone in load"


def test_watermark_rises_under_pressure_and_decays():
    """
    Watermark should climb when incoming tx min-fees are consistently above it,
    and decay (by some factor) when pressure subsides.
    """
    wm_mod = pytest.importorskip(
        "mempool.watermark", reason="mempool.watermark module not found"
    )

    # Construct watermark
    wm = None
    for cls_name in ("Watermark", "RollingWatermark", "MinFeeWatermark"):
        ctor = _get_attr_any(wm_mod, [cls_name])
        if ctor:
            try:
                # Common knobs: rise_alpha/decay_alpha or half_life
                wm = ctor(rise_alpha=0.5, decay_alpha=0.5)  # type: ignore[call-arg]
            except TypeError:
                wm = ctor()  # type: ignore[call-arg]
            break
    if wm is None:
        pytest.skip("No known watermark class found")

    # Accessors
    def get_w() -> float:
        v = _get_attr_any(wm, ["value", "current", "min_fee", "floor", "get"])
        if isinstance(v, (int, float)):
            return float(v)
        if callable(v):
            return float(v())
        raise AssertionError("No watermark getter found")

    def update_rise(x: float) -> None:
        fn = _get_attr_any(wm, ["observe", "update", "rise", "ingest"])
        assert callable(fn), "No watermark update method"
        try:
            fn(x)  # type: ignore[misc]
        except TypeError:
            fn({"fee": x})  # type: ignore[misc]

    def decay_step() -> None:
        fn = _get_attr_any(wm, ["decay", "tick", "relax"])
        if callable(fn):
            fn()  # type: ignore[misc]

    # Start low, then push pressure
    for _ in range(5):
        update_rise(50)
    low = get_w()

    for _ in range(5):
        update_rise(500)
    high = get_w()
    assert (
        high > low + 10
    ), f"watermark did not rise under pressure: low={low}, high={high}"

    # Now let it decay
    for _ in range(5):
        decay_step()
    after_decay = get_w()
    assert (
        after_decay < high - 5
    ), f"watermark did not decay: high={high}, after={after_decay}"
