from __future__ import annotations

import math

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


# ---------------------------------------------------------------------------
# Step-up bound: thresholds cannot jump faster than max_step_up
# ---------------------------------------------------------------------------


def test_step_up_is_bounded_by_max_step_up() -> None:
    wm = _mk_watermark()
    cfg = wm.cfg

    # Start from baseline: admit_floor = min_floor_wei.
    base_floor = cfg.min_floor_wei

    # Feed an extremely high-fee block so the "target" wants to jump far above.
    high_block_fees = [cfg.min_floor_wei * 1_000] * 50
    wm.observe_block_inclusions(high_block_fees)

    # Force a thresholds computation under high utilization.
    th: Thresholds = wm.thresholds(pool_size=90, capacity=100)
    new_floor = th.admit_floor_wei

    # max_step_up is a multiplicative cap per thresholds() call.
    limit = math.ceil(base_floor * cfg.max_step_up)

    # Qualitative guarantee: we should not exceed that limit.
    assert new_floor <= limit
    assert new_floor >= base_floor  # cannot move *down* on an upward target


# ---------------------------------------------------------------------------
# Step-down bound: thresholds cannot collapse faster than max_step_down
# ---------------------------------------------------------------------------


def test_step_down_is_bounded_by_max_step_down() -> None:
    wm = _mk_watermark()
    cfg = wm.cfg

    # First, push the watermark "up" by repeatedly feeding high-fee blocks
    # and computing thresholds under high utilization.
    high_block_fees = [cfg.min_floor_wei * 100] * 50
    for _ in range(5):
        wm.observe_block_inclusions(high_block_fees)
        th = wm.thresholds(pool_size=95, capacity=100)
    high_floor = th.admit_floor_wei
    assert high_floor > cfg.min_floor_wei

    # Now feed very low-fee blocks so the target wants to move down sharply.
    low_block_fees = [cfg.min_floor_wei] * 50
    wm.observe_block_inclusions(low_block_fees)

    th2 = wm.thresholds(pool_size=95, capacity=100)
    new_floor = th2.admit_floor_wei

    # Compute the minimum allowed by max_step_down.
    min_allowed = math.floor(high_floor * cfg.max_step_down)

    # We expect the floor not to drop faster than the down-step cap.
    # (And, trivially, not below the global minimum.)
    assert new_floor >= min_allowed
    assert new_floor >= cfg.min_floor_wei
    # Note: depending on the EMA target, the floor may stay flat or even
    # tick up slightly; we only assert the *lower* bound here.


# ---------------------------------------------------------------------------
# Alternating high/low fees: EMA + bounded steps avoid wild oscillation
# ---------------------------------------------------------------------------


def test_ema_smoothing_avoids_wild_oscillation() -> None:
    """
    Feed alternating high/low fee blocks and ensure that the published
    admit_floor moves gradually (bounded by max_step_up/down), rather than
    snapping to extremes.

    We do not assert exact numeric EMA values, only that successive steps
    are within the configured bounds.
    """
    wm = _mk_watermark()
    cfg = wm.cfg

    high_fee = cfg.min_floor_wei * 50
    low_fee = cfg.min_floor_wei

    floors = []

    # Start with a couple of high-fee blocks to move the floor up a bit.
    for _ in range(3):
        wm.observe_block_inclusions([high_fee] * 10)
        th = wm.thresholds(pool_size=80, capacity=100)
        floors.append(th.admit_floor_wei)

    # Then alternate low/high/low/high under moderate utilization.
    for block_fees in ([low_fee] * 10, [high_fee] * 10, [low_fee] * 10, [high_fee] * 10):
        wm.observe_block_inclusions(block_fees)
        th = wm.thresholds(pool_size=80, capacity=100)
        floors.append(th.admit_floor_wei)

    # Check pairwise that each step obeys the max_step_up/down bounds.
    for prev, cur in zip(floors, floors[1:]):
        if prev == 0:
            # First non-zero step is handled separately inside FeeWatermark.
            continue
        if cur >= prev:
            # Upward move: must be <= max_step_up factor.
            assert cur <= math.ceil(prev * cfg.max_step_up)
        else:
            # Downward move: must be >= max_step_down factor.
            assert cur >= math.floor(prev * cfg.max_step_down)

        # Floors should never dip below the configured minimum.
        assert cur >= cfg.min_floor_wei
