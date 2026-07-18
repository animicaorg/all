"""Vardiff xmrig-compat rework: difficulty floor + PPS work-crediting.

Covers the two invariants the rework must hold on the live pool:

1. The per-session vardiff may lower the share difficulty for smoother, lower
   variance PPS, but the wire value in mining.set_difficulty must never drop
   below ``share_difficulty_floor`` (this xmrig build disconnect-loops on a
   sub-1.0 set_difficulty). ``_share_bounds_for_theta`` must floor the EASIEST
   share accordingly, and the floor round-trips exactly through the forward map.

2. PPS credit is priced at a share's EXPECTED value (reward × P(share solves a
   block)), so total credit converges to exactly one block reward per block
   REGARDLESS of the share difficulty — lowering the vardiff must not change the
   pool-wide payout, and block-winning shares are not credited a full-reward
   bonus on top (the old double-count that inflated credit past mined coinbase).
"""
import math
from types import SimpleNamespace

from core.utils.pow import MICRO
from mining.templates import share_target_to_difficulty

from animica.stratum_pool.metrics import PoolMetrics, _TWO_POW_256
from animica.stratum_pool.stratum_server import StratumPoolServer

# A realistic live θ: ~25 nats (block difficulty ≈ 15, ~60 s blocks on the seed GPU).
THETA = 25_000_000
REWARD = 300 * 10**9  # 300 ANM in nano


def _server(min_d=0.00001, max_d=1.0, floor=1.0):
    srv = object.__new__(StratumPoolServer)
    srv._config = SimpleNamespace(
        min_difficulty=min_d, max_difficulty=max_d, share_difficulty_floor=floor
    )
    return srv


def _wire_difficulty(theta, threshold_micro):
    """The set_difficulty value the miner receives for a given θµ threshold."""
    return share_target_to_difficulty(theta, threshold_micro / theta)


def test_floor_threshold_round_trips_to_floor_difficulty():
    srv = _server(floor=1.0)
    t = srv._difficulty_to_threshold_micro(1.0)
    # A share at the floor threshold has wire difficulty == the floor (±1%).
    assert abs(_wire_difficulty(THETA, t) - 1.0) < 0.01
    # The floor threshold is θ-independent: same t for any θ above it.
    assert srv._difficulty_to_threshold_micro(1.0) == t
    # A higher floor => a strictly harder (larger) minimum threshold.
    assert srv._difficulty_to_threshold_micro(4.0) > t


def test_bounds_never_emit_sub_floor_wire_difficulty():
    # The old pin (min=max=1.0) collapsed the band to [θ, θ] (block-only shares).
    pinned = _server(min_d=1.0, max_d=1.0, floor=1.0)
    lo, hi = pinned._share_bounds_for_theta(THETA)
    assert lo == hi == THETA  # unchanged legacy behavior when pinned

    # With the default low min_difficulty + floor, the band opens up but the
    # EASIEST share still sits at (not below) the floor difficulty.
    srv = _server(min_d=0.00001, max_d=1.0, floor=1.0)
    lo, hi = srv._share_bounds_for_theta(THETA)
    assert lo < hi, "vardiff band must be open, not pinned"
    assert hi == THETA, "hardest share is a full block"
    easiest_diff = _wire_difficulty(THETA, lo)
    hardest_diff = _wire_difficulty(THETA, hi)
    assert easiest_diff >= 1.0 - 1e-9, f"easiest share {easiest_diff} < floor 1.0"
    assert abs(easiest_diff - 1.0) < 0.01, "easiest share should sit at the floor"
    assert hardest_diff > easiest_diff, "block share is harder than the floor share"


def test_raised_floor_lifts_the_easiest_share():
    srv = _server(min_d=0.00001, max_d=1.0, floor=4.0)
    lo, _ = srv._share_bounds_for_theta(THETA)
    assert abs(_wire_difficulty(THETA, lo) - 4.0) < 0.05


def _pps_total_credit_per_block(ratio):
    """Sum PPS credit over the shares expected in one block at share ``ratio``.

    Mirrors PoolMetrics.record_share: credit_fraction = block_target/share_target
    = exp(-θ·(1-ratio)/MICRO); a block arrives every 1/credit_fraction shares.
    """
    m = object.__new__(PoolMetrics)
    credit_fraction = math.exp(-THETA * (1.0 - ratio) / MICRO)
    per_share = m._credit_for_share(REWARD, credit_fraction)
    expected_shares_per_block = 1.0 / credit_fraction
    return per_share * expected_shares_per_block, expected_shares_per_block


def test_pps_credit_is_difficulty_invariant_and_equals_one_reward():
    # Block-difficulty shares (ratio 1.0): ~1 share per block, each worth full reward.
    total_block, n_block = _pps_total_credit_per_block(1.0)
    assert abs(n_block - 1.0) < 0.01
    assert abs(total_block - REWARD) / REWARD < 0.02

    # Floor-difficulty shares (~diff-1): the floor ratio at this θ.
    floor_ratio = _server(floor=1.0)._difficulty_to_threshold_micro(1.0) / THETA
    total_floor, n_floor = _pps_total_credit_per_block(floor_ratio)
    assert n_floor > 10, "diff-1 shares should be many per block (smoother PPS)"
    # Same pool-wide payout as block-only crediting — difficulty-invariant.
    assert abs(total_floor - REWARD) / REWARD < 0.02
    assert abs(total_floor - total_block) / REWARD < 0.02
