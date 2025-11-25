from __future__ import annotations

import pytest

wm_mod = pytest.importorskip(
    "mempool.watermark", reason="mempool.watermark module not found"
)

WatermarkConfig = wm_mod.WatermarkConfig
FeeWatermark = wm_mod.FeeWatermark
Thresholds = wm_mod.Thresholds


def _mk_watermark() -> FeeWatermark:
    # Use mostly default config; that’s what the policy is tuned for.
    cfg = WatermarkConfig()
    return FeeWatermark(cfg)


# ---------------------------------------------------------------------------
# Low utilization: no eviction, floor >= min_floor_wei
# ---------------------------------------------------------------------------


def test_low_utilization_has_no_eviction_and_respects_min_floor() -> None:
    wm = _mk_watermark()
    cfg = wm.cfg

    # Feed some modest block fees so histogram / EMA are non-trivial.
    block_fees = [
        cfg.min_floor_wei,
        cfg.min_floor_wei * 2,
        cfg.min_floor_wei * 3,
    ]
    wm.observe_block_inclusions(block_fees)

    # Low utilization: pool 10% full (< low_util = 0.60).
    th: Thresholds = wm.thresholds(pool_size=10, capacity=100)

    # Qualitative expectations:
    # - No eviction pressure at low utilization.
    # - Admit floor never drops below the configured minimum.
    assert th.evict_below_wei == 0
    assert th.admit_floor_wei >= cfg.min_floor_wei
    assert 0.0 <= th.utilization <= cfg.low_util


# ---------------------------------------------------------------------------
# High utilization: eviction enabled, evict_below >= admit_floor >= min_floor
# ---------------------------------------------------------------------------


def test_high_utilization_enables_eviction_and_keeps_evict_at_least_floor() -> None:
    wm = _mk_watermark()
    cfg = wm.cfg

    # Seed with some realistic block fees (all comfortably above min_floor_wei).
    block_fees = [
        cfg.min_floor_wei * 2,
        cfg.min_floor_wei * 4,
        cfg.min_floor_wei * 8,
        cfg.min_floor_wei * 10,
    ]
    wm.observe_block_inclusions(block_fees)

    # High utilization: pool 95% full (> high_util = 0.90).
    th: Thresholds = wm.thresholds(pool_size=95, capacity=100)

    # Under high pressure we expect:
    # - eviction threshold to be active (> 0),
    # - admission floor to be at least the eviction floor,
    # - both at or above the configured minimum.
    assert th.evict_below_wei > 0
    assert th.admit_floor_wei >= cfg.min_floor_wei
    assert th.evict_below_wei >= th.admit_floor_wei
    assert th.utilization >= cfg.high_util


# ---------------------------------------------------------------------------
# Higher recent fees → higher thresholds (qualitatively)
# ---------------------------------------------------------------------------


def test_higher_recent_block_fees_raise_thresholds() -> None:
    """
    Compare two independent watermarks:

      - wm_low sees only low-ish block fees.
      - wm_high sees much higher block fees.

    Under the same high-utilization conditions, wm_high should produce
    admit/evict thresholds that are >= wm_low's (qualitatively "higher
    pressure" corresponding to a richer fee environment).
    """
    cfg_low = WatermarkConfig()
    cfg_high = WatermarkConfig()

    wm_low = FeeWatermark(cfg_low)
    wm_high = FeeWatermark(cfg_high)

    low_block_fees = [cfg_low.min_floor_wei] * 10
    high_block_fees = [cfg_high.min_floor_wei * 10] * 10

    wm_low.observe_block_inclusions(low_block_fees)
    wm_high.observe_block_inclusions(high_block_fees)

    # Same high utilization for both.
    th_low: Thresholds = wm_low.thresholds(pool_size=90, capacity=100)
    th_high: Thresholds = wm_high.thresholds(pool_size=90, capacity=100)

    # Qualitative relation: high-fee environment should not yield *lower*
    # thresholds than the low-fee environment.
    assert th_high.admit_floor_wei >= th_low.admit_floor_wei
    assert th_high.evict_below_wei >= th_low.evict_below_wei


# ---------------------------------------------------------------------------
# Monotonicity with utilization: admit_floor should not drop as utilization rises
# ---------------------------------------------------------------------------


def test_admit_floor_is_monotonic_in_utilization() -> None:
    """
    For a fixed fee environment, as pool utilization increases from low
    to mid to high, the admission floor should not *decrease*.

    Smoothing via _bounded_step may cap how fast it rises, but it should
    be non-decreasing across a single watermark instance as utilization
    increases.
    """
    wm = _mk_watermark()
    cfg = wm.cfg

    # Seed with some block fees to populate EMA + histogram.
    wm.observe_block_inclusions(
        [
            cfg.min_floor_wei * 2,
            cfg.min_floor_wei * 3,
            cfg.min_floor_wei * 5,
        ]
    )

    # Increasing utilizations: 20% (low), 70% (mid), 95% (high).
    th_low: Thresholds = wm.thresholds(pool_size=20, capacity=100)
    th_mid: Thresholds = wm.thresholds(pool_size=70, capacity=100)
    th_high: Thresholds = wm.thresholds(pool_size=95, capacity=100)

    assert th_low.admit_floor_wei <= th_mid.admit_floor_wei <= th_high.admit_floor_wei
