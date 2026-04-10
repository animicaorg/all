from __future__ import annotations

import pytest

wm_mod = pytest.importorskip(
    "mempool.watermark", reason="mempool.watermark module not found"
)

WatermarkConfig = wm_mod.WatermarkConfig
FeeWatermark = wm_mod.FeeWatermark
Thresholds = wm_mod.Thresholds


def _mk_watermark() -> FeeWatermark:
    cfg = WatermarkConfig()
    return FeeWatermark(cfg)


def _hist_min(cfg: WatermarkConfig) -> int:
    # Prefer explicit histogram min if present, otherwise fall back to min_floor.
    if hasattr(cfg, "hist_min_wei"):
        return int(getattr(cfg, "hist_min_wei"))
    return int(cfg.min_floor_wei)


def _hist_max(cfg: WatermarkConfig) -> int:
    # Prefer explicit histogram max if present; otherwise choose a generous multiple.
    if hasattr(cfg, "hist_max_wei"):
        return int(getattr(cfg, "hist_max_wei"))
    # Fallback: "soft" upper bound used only for qualitative checks.
    return int(cfg.min_floor_wei) * 10_000_000


# ---------------------------------------------------------------------------
# Invariant: thresholds never dip below min_floor_wei
# ---------------------------------------------------------------------------


def test_thresholds_never_below_min_floor_across_utilization() -> None:
    wm = _mk_watermark()
    cfg = wm.cfg

    # Drive some history so EMA + histogram are non-trivial.
    wm.observe_block_inclusions(
        [
            cfg.min_floor_wei,
            cfg.min_floor_wei * 2,
            cfg.min_floor_wei * 5,
            cfg.min_floor_wei * 10,
        ]
    )

    capacity = 100

    for pool_size in [0, 10, 50, 90, 100]:
        th: Thresholds = wm.thresholds(pool_size=pool_size, capacity=capacity)

        # Core invariant: never below configured minimum.
        assert th.admit_floor_wei >= cfg.min_floor_wei
        if th.evict_below_wei > 0:
            assert th.evict_below_wei >= cfg.min_floor_wei


# ---------------------------------------------------------------------------
# Invariant: thresholds respect histogram bounds (if configured)
# ---------------------------------------------------------------------------


def test_thresholds_remain_within_histogram_bounds() -> None:
    wm = _mk_watermark()
    cfg = wm.cfg

    hist_min = _hist_min(cfg)
    hist_max = _hist_max(cfg)

    # Feed a mixture of very low and very high fees to exercise the extremes.
    fee_patterns = [
        [hist_min] * 10,
        [hist_min * 2] * 10,
        [hist_max // 4] * 10,
        [hist_max // 2] * 10,
        [hist_max] * 10,
    ]
    for fees in fee_patterns:
        wm.observe_block_inclusions(fees)

    capacity = 100

    for pool_size in [0, 20, 60, 95, 100]:
        th: Thresholds = wm.thresholds(pool_size=pool_size, capacity=capacity)

        # Admit floor should always lie within [hist_min, hist_max],
        # and never drop below min_floor_wei.
        assert hist_min <= th.admit_floor_wei <= hist_max
        assert th.admit_floor_wei >= cfg.min_floor_wei

        # Eviction threshold, when active, must also lie within bounds.
        if th.evict_below_wei > 0:
            assert hist_min <= th.evict_below_wei <= hist_max
            assert th.evict_below_wei >= cfg.min_floor_wei
