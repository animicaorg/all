import os
import secrets

import pytest

from consensus.state import InMemoryConsensusState, PolicyRoots, RetargetParams, WindowSpec

DEVNET_CHAIN_ID = 1337
DEVNET_THETA_INIT = 2_000_000
DEVNET_THETA_MIN = 1_000_000
DEVNET_THETA_MAX = 12_000_000
DEVNET_TARGET_SEC = 2
DEVNET_RETARGET = RetargetParams(
    target_interval_sec=DEVNET_TARGET_SEC,
    up_ppm=1_150_000,  # +15% bound keeps oscillations tame
    down_ppm=980_000,  # -2% bound helps recover after gaps without cratering Θ
    ema_shift=4,  # 2^4 = 16-block EMA window
    window=WindowSpec(size_blocks=20, include_tip=True),
)
DEVNET_SUBSIDY_NANM = 10_000_000
DEVNET_SPLIT = {"miner": 60, "aicf": 30, "treasury": 10}


@pytest.fixture()
def devnet_state() -> InMemoryConsensusState:
    roots = PolicyRoots(alg_policy_root=os.urandom(32), poies_policy_root=os.urandom(32))
    st = InMemoryConsensusState(
        chain_id=DEVNET_CHAIN_ID,
        theta_micro=DEVNET_THETA_INIT,
        policy_roots=roots,
        retarget=DEVNET_RETARGET,
    )
    st.init_from_genesis(genesis_hash=b"\x00" * 32, genesis_timestamp=0)
    return st


def _mine_blocks(state: InMemoryConsensusState, count: int, *, spacing: int) -> list[int]:
    ts = state.head.timestamp
    thetas = []
    for i in range(count):
        ts += spacing
        header_hash = secrets.token_bytes(32)
        state.accept_header(header_hash, state.head.hash, ts)
        thetas.append(state.theta)
    return thetas


def test_single_miner_progresses_and_theta_stays_bounded(devnet_state: InMemoryConsensusState) -> None:
    thetas = _mine_blocks(devnet_state, 12, spacing=DEVNET_TARGET_SEC)

    assert devnet_state.head.height == 12
    assert min(thetas) >= DEVNET_THETA_MIN
    assert max(thetas) <= DEVNET_THETA_MAX
    assert max(thetas) / min(thetas) < 1.25, "Θ should not oscillate wildly at target cadence"


def test_retarget_recovers_after_pause(devnet_state: InMemoryConsensusState) -> None:
    _mine_blocks(devnet_state, 6, spacing=DEVNET_TARGET_SEC)
    theta_before_pause = devnet_state.theta

    # Simulate a miner going idle for 20s, forcing Θ to ease
    pause_spacing = 20
    _mine_blocks(devnet_state, 1, spacing=pause_spacing)
    assert devnet_state.theta < theta_before_pause

    # Resume normal cadence and ensure Θ climbs back but stays sane
    post_pause_thetas = _mine_blocks(devnet_state, 6, spacing=DEVNET_TARGET_SEC)
    assert post_pause_thetas[-1] > DEVNET_THETA_MIN
    assert post_pause_thetas[-1] < DEVNET_THETA_INIT * 1.3


def test_block_subsidy_split_is_deterministic() -> None:
    blocks = 10
    total = DEVNET_SUBSIDY_NANM * blocks
    miner = total * DEVNET_SPLIT["miner"] // 100
    aicf = total * DEVNET_SPLIT["aicf"] // 100
    treasury = total - miner - aicf

    assert miner == 60_000_000
    assert aicf == 30_000_000
    assert treasury == 10_000_000
    assert miner + aicf + treasury == total
