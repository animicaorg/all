"""FORK_SERVICE_CARVE + FORK_TREASURY_25 (9.7.0) — the reward split at block 75,000.

From the activation height a fixed 25% of every block's subsidy is reserved for
inference/service claims, WITHHELD FROM THE MINER whether or not anything claims
it (this replaces the self-gating carve where "mine without serving" kept 100%),
and the foundation-treasury share simultaneously rises 15% -> 25%. The net split
becomes 50% miner / 25% treasury / 25% inference. Where the inference slice lands is
decided by one question — did anyone claim in this block? Nothing claimed: it rolls to
the foundation treasury, so the block is 50% miner / 50% treasury. ANY claim (10.2.5):
the WHOLE slice is paid to the claimants pro-rata and the treasury takes no share of it,
so a provider anchored for a small claim receives the entire carve of that block.

The property that must never break is emission conservation: paid + residual ==
carve and miner + carve == the pre-carve subsidy. A bug here mints or burns coin
every block.
"""

from __future__ import annotations

from consensus.rewards import (
    FOUNDATION_TREASURY_ADDRESS,
    SERVICE_CARVE_PCT_V1,
    TREASURY_SPLIT_PCT_V2,
    compute_block_reward,
    service_carve_pct,
)
from consensus.service_carve import carve_amount, split_carve
from core.utils.address import address_to_bytes

BELOW, AT = 74_999, 75_000
TREASURY = address_to_bytes(FOUNDATION_TREASURY_ADDRESS)
PROVIDER = b"\x44" * 32

# Mainnet genesis-epoch numbers. FORK_TREASURY_25 takes 25% inside
# compute_block_reward, so the carve region sees TOTAL=300, treasury=75, miner=225.
TOTAL = 300_000_000_000
TREASURY_AMT = TOTAL * TREASURY_SPLIT_PCT_V2 // 100   # 75 ANM
MINER_IN = TOTAL - TREASURY_AMT                       # 225 ANM (75%)


# --- gating -----------------------------------------------------------------

def test_the_boundary_is_exactly_75000():
    assert service_carve_pct(BELOW) == 0
    assert service_carve_pct(AT) == SERVICE_CARVE_PCT_V1 == 25


def test_history_replays_unchanged_below_the_fork():
    for h in (0, 42_001, 50_000, 70_000, 74_999):
        assert service_carve_pct(h) == 0, h


def test_testnet_and_devnet_have_it_from_genesis():
    for chain_id in (2, 1337):
        assert service_carve_pct(0, chain_id=chain_id) == SERVICE_CARVE_PCT_V1, chain_id


# --- compute_block_reward: treasury rises to 25% at 75,000 ------------------

def _split(height):
    outs = compute_block_reward(1, height, params=_PARAMS)
    total = sum(a for _, a in outs)
    miner = sum(a for ad, a in outs if ad.startswith("anim1coinbase"))
    treasury = sum(a for ad, a in outs if ad == FOUNDATION_TREASURY_ADDRESS)
    return miner, treasury, total


from core.chain.block_import import _load_full_params_dict  # noqa: E402
_PARAMS = _load_full_params_dict(1)


def test_treasury_is_15pct_below_and_25pct_at_75000():
    m, t, tot = _split(74_999)
    assert t * 100 // tot == 15 and m * 100 // tot == 85
    m, t, tot = _split(75_000)
    assert t * 100 // tot == 25 and m * 100 // tot == 75


# --- the carve arithmetic (consensus passes treasury as the residual sink) ---

def test_the_whole_carve_goes_to_treasury_when_nobody_claims_it():
    """The ordinary case today: no anchors, and the miner STILL loses the slice —
    which rolls to the treasury (operator rule), giving a 50/50 block."""
    miner, outs, carve = split_carve(
        miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
        anchor_outputs=None, escrow_address=TREASURY, treasury_address=TREASURY)
    assert carve == TOTAL * 25 // 100
    assert miner == MINER_IN - carve
    assert outs == [(TREASURY, carve)]
    # full block: miner 50%, treasury base 25% + carve 25% == 50%
    assert miner * 100 // TOTAL == 50
    assert (TREASURY_AMT + carve) * 100 // TOTAL == 50


def test_any_claim_pays_the_WHOLE_carve_to_providers():
    """10.2.5: if a block has inference in it, all of the slice is paid — the
    treasury takes no share. A 1 ANM claim receives the entire 75 ANM carve."""
    claim = [(PROVIDER, 1_000_000_000)]
    miner, outs, carve = split_carve(
        miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
        anchor_outputs=claim, escrow_address=TREASURY, treasury_address=TREASURY)
    assert outs == [(PROVIDER, carve)]         # scaled up from 1 ANM to the full carve
    assert TREASURY not in dict(outs)          # nothing falls to the treasury
    assert miner == MINER_IN - carve


def test_the_whole_carve_is_split_pro_rata_between_claimants():
    """Claim size sets the split BETWEEN providers, not the total they receive."""
    other = b"\x55" * 32
    claim = [(PROVIDER, 3_000_000_000), (other, 1_000_000_000)]   # 3:1
    miner, outs, carve = split_carve(
        miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
        anchor_outputs=claim, escrow_address=TREASURY, treasury_address=TREASURY)
    d = dict(outs)
    assert sum(d.values()) == carve            # all of it paid out
    assert d[PROVIDER] == carve * 3 // 4
    assert d[other] == carve // 4
    assert TREASURY not in d


def test_scale_up_is_integer_exact_and_deterministic():
    """The floor remainder must land somewhere fixed, or two honest nodes compute
    different outputs and the chain splits."""
    claim = [(PROVIDER, 1), (b"\x55" * 32, 1), (b"\x66" * 32, 1)]   # 75 ANM / 3 has a remainder
    for _ in range(5):
        _, outs, carve = split_carve(
            miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
            anchor_outputs=claim, escrow_address=TREASURY, treasury_address=TREASURY)
        assert sum(a for _, a in outs) == carve
        assert outs == split_carve(
            miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
            anchor_outputs=claim, escrow_address=TREASURY, treasury_address=TREASURY)[1]
    # the remainder goes to the FIRST anchor entry, by anchor order
    assert outs[0][0] == PROVIDER
    assert outs[0][1] >= outs[1][1]


def test_fully_claimed_block_is_50_25_25():
    claim = [(PROVIDER, TOTAL * 25 // 100)]
    miner, outs, carve = split_carve(
        miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
        anchor_outputs=claim, escrow_address=TREASURY, treasury_address=TREASURY)
    paid = dict(outs).get(PROVIDER, 0)
    assert miner * 100 // TOTAL == 50          # miner 50%
    assert TREASURY_AMT * 100 // TOTAL == 25   # base treasury 25%
    assert paid * 100 // TOTAL == 25           # inference 25%


def test_emission_is_conserved_however_the_carve_is_split():
    for claim in (None, [], [(PROVIDER, 1)], [(PROVIDER, TOTAL * 25 // 100)],
                  [(PROVIDER, 5), (b"\x55" * 32, 7)]):
        miner, outs, carve = split_carve(
            miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
            anchor_outputs=claim, escrow_address=TREASURY, treasury_address=TREASURY)
        assert sum(a for _, a in outs) == carve, claim
        assert miner + carve == MINER_IN, claim


def test_anchors_can_never_draw_more_than_was_withheld():
    greedy = [(PROVIDER, TOTAL)]           # asks for the entire subsidy
    miner, outs, carve = split_carve(
        miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
        anchor_outputs=greedy, escrow_address=TREASURY, treasury_address=TREASURY)
    assert sum(a for _, a in outs) == carve
    assert carve == carve_amount(TOTAL, 25)
