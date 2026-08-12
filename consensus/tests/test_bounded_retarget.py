"""FORK_BOUNDED_RETARGET (9.5.0) — the one-way trapdoor in the difficulty retarget.

The deployed emergency valve assigns `theta = theta_min_micro` DIRECTLY, bypassing
`step_clamp_micro`. On live mainnet params that is 26,821,504 -> 12,000,000 µ-nats in a
single block, and theta is log-work, so per-block work falls by e^14.8 ≈ 2.7 million
times from ONE slow block.

It then never recovers: at the floor `min_block_spacing_ms` pins dt to exactly the
target, so r_k = ln(dt/T) = ln(1) = 0. The error signal is identically zero, tau never
rises, and theta stays on the floor forever. With a single block producer (100% of
recent blocks come from one coinbase) one stalled pool is all it takes to cripple the
chain permanently.

These tests use the REAL mainnet retarget parameters, because the trapdoor is a property
of those specific numbers.
"""

from __future__ import annotations

import math

import pytest

import consensus.difficulty as d

# Live mainnet values (spec/params.yaml chain 1 -> _build_retarget_params), captured
# 2026-08-09 with theta at 26,821,504 µ-nats.
MAINNET = d.RetargetParams(
    target_block_time_s=60.0,
    half_life_blocks=4.265,
    gain_beta=0.5,
    step_clamp_micro=162_518,
    theta_min_micro=12_000_000,
    theta_max_micro=3_000_000_000,
    max_block_time_s=3600.0,
)
THETA_LIVE = 26_821_504
BELOW = 69_999      # below FORK_BOUNDED_RETARGET
AT = 75_000         # the activation height
STALL_DT = 4_000.0  # exceeds max_block_time_s = 3600


def _fresh(theta=THETA_LIVE):
    return d.init_state(MAINNET, theta)


def test_the_trapdoor_still_exists_below_the_fork():
    """Grandfathered: history must replay byte-identically, trapdoor and all. If this
    ever starts passing differently, old blocks would recompute a different theta."""
    st = d.update_theta(_fresh(), dt_seconds=STALL_DT, height=BELOW)
    assert st.theta_micro == MAINNET.theta_min_micro, "legacy valve teleports to the floor"
    for _ in range(50):
        st = d.update_theta(st, dt_seconds=60.0, height=BELOW)
    assert st.theta_micro == MAINNET.theta_min_micro, (
        "legacy is PERMANENTLY stuck: dt pinned at target makes r_k exactly 0")


def test_one_stalled_block_no_longer_erases_the_work_level():
    st = d.update_theta(_fresh(), dt_seconds=STALL_DT, height=AT)
    assert st.theta_micro > MAINNET.theta_min_micro
    lost = math.exp((THETA_LIVE - st.theta_micro) / 1e6)
    assert lost < 10, f"work should fall by a small factor, not {lost:,.0f}x"
    # And the descent is exactly the bounded step, not an arbitrary amount.
    assert THETA_LIVE - st.theta_micro == MAINNET.step_clamp_micro * d.EMERGENCY_STEP_MULTIPLE


def test_theta_holds_after_the_stall_instead_of_drifting_down():
    """The first cut of this fix recorded ln(4000/60)=4.2 as the EMA, and the ordinary
    controller then spent 30 blocks working that memory off — theta slid 26.17 -> 22.79
    nats and quietly undid the clamp. The EMA is reset instead."""
    st = d.update_theta(_fresh(), dt_seconds=STALL_DT, height=AT)
    after_stall = st.theta_micro
    for _ in range(30):
        st = d.update_theta(st, dt_seconds=60.0, height=AT)
    assert st.theta_micro == after_stall, "on-target blocks must not move theta"


def test_a_prolonged_stall_still_eases_difficulty_progressively():
    """The valve must remain useful: a chain that really cannot find blocks still gets
    easier, one bounded step per block, driven by fresh evidence each time."""
    st = _fresh()
    seen = []
    for _ in range(10):
        st = d.update_theta(st, dt_seconds=STALL_DT, height=AT)
        seen.append(st.theta_micro)
    assert seen == sorted(seen, reverse=True), "each stalled block must ease difficulty"
    assert seen[-1] < seen[0]
    assert seen[-1] > MAINNET.theta_min_micro, "ten stalls must not reach the floor"


def test_the_valve_can_still_reach_the_floor_but_only_by_stepping():
    """It must never overshoot below the floor, however many stalls occur."""
    st = _fresh()
    for _ in range(500):
        st = d.update_theta(st, dt_seconds=STALL_DT, height=AT)
    assert st.theta_micro == MAINNET.theta_min_micro


def test_the_floor_escape_climbs_where_legacy_is_stuck():
    forked = _fresh(MAINNET.theta_min_micro)
    legacy = _fresh(MAINNET.theta_min_micro)
    for _ in range(5):
        forked = d.update_theta(forked, dt_seconds=60.0, height=AT)
        legacy = d.update_theta(legacy, dt_seconds=60.0, height=BELOW)
    assert forked.theta_micro > MAINNET.theta_min_micro, "must escape the floor"
    assert legacy.theta_micro == MAINNET.theta_min_micro, "legacy stays pinned"


def test_the_floor_escape_does_not_fire_when_blocks_are_genuinely_slow():
    """At the floor with SLOW blocks the chain is struggling; ratcheting up then would
    be exactly wrong."""
    st = _fresh(MAINNET.theta_min_micro)
    st = d.update_theta(st, dt_seconds=600.0, height=AT)   # 10x target, under max
    assert st.theta_micro == MAINNET.theta_min_micro, "must not climb while slow"


def test_the_escape_respects_theta_max():
    tight = d.RetargetParams(**{**MAINNET.__dict__, "theta_min_micro": 12_000_000,
                                "theta_max_micro": 12_050_000})
    st = d.init_state(tight, 12_000_000)
    for _ in range(20):
        st = d.update_theta(st, dt_seconds=60.0, height=AT)
    assert st.theta_micro <= tight.theta_max_micro


def test_height_none_keeps_the_legacy_path():
    """Simulators and tooling call update_theta without a height; they must not silently
    get consensus-forked behaviour."""
    st = d.update_theta(_fresh(), dt_seconds=STALL_DT)
    assert st.theta_micro == MAINNET.theta_min_micro


def test_testnet_and_devnet_have_the_fork_from_genesis():
    for chain_id in (2, 1337):
        st = d.update_theta(_fresh(), dt_seconds=STALL_DT, height=0, chain_id=chain_id)
        assert st.theta_micro > MAINNET.theta_min_micro, chain_id


@pytest.mark.parametrize("dt", [0.0, -1.0, float("inf"), float("nan")])
def test_pathological_intervals_are_still_ignored(dt):
    st = _fresh()
    assert d.update_theta(st, dt_seconds=dt, height=AT).theta_micro == st.theta_micro
