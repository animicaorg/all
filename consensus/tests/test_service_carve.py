"""FORK_SERVICE_CARVE (9.5.0) — the service slice is withheld whether or not it is claimed.

The rule replaces a SELF-GATING carve (no anchors -> no carve -> miner keeps 100%, which
is exactly how "mining without serving earns the full reward" was possible) with an
unconditional one where anchors choose only the destination.

The property that must never break is emission conservation: paid + residual == carve,
and miner + carve == the pre-carve subsidy. A bug here mints or burns coin every block.
"""

from __future__ import annotations

import pytest

from consensus.rewards import (
    SERVICE_CARVE_PCT_V1,
    SERVICE_ESCROW_ADDRESS,
    service_carve_pct,
)
from consensus.service_carve import ServiceCarveError, carve_amount, split_carve
from core.utils.address import address_to_bytes

BELOW, AT = 74_999, 75_000
ESCROW = address_to_bytes(SERVICE_ESCROW_ADDRESS)
PROVIDER = b"\x44" * 32
# The real mainnet genesis-epoch numbers. FORK_TREASURY_25 takes 25% inside
# compute_block_reward, so the settlement region sees TOTAL=300 and MINER=225 ANM.
TOTAL = 300_000_000_000
TREASURY = TOTAL * 25 // 100          # 75 ANM
MINER_IN = TOTAL - TREASURY           # 225 ANM, what the carve is subtracted from


# --- gating -----------------------------------------------------------------

def test_the_boundary_is_exactly_75000():
    assert service_carve_pct(74_999) == 0
    assert service_carve_pct(75_000) == SERVICE_CARVE_PCT_V1 == 25


def test_history_replays_unchanged_below_the_fork():
    for h in (0, 42_001, 50_000, 70_000, 74_999):
        assert service_carve_pct(h) == 0, h


def test_testnet_and_devnet_have_it_from_genesis():
    for chain_id in (2, 1337):
        assert service_carve_pct(0, chain_id=chain_id) == SERVICE_CARVE_PCT_V1, chain_id


# --- the escrow destination must be real ------------------------------------

def test_the_escrow_address_resolves_to_a_real_32_byte_account():
    """An address that does not decode, or a zero key, BURNS the residual every block."""
    assert len(ESCROW) == 32
    assert any(ESCROW), "escrow must not be the zero account"


# --- the arithmetic ---------------------------------------------------------

def test_the_whole_carve_goes_to_escrow_when_nobody_claims_it():
    """The ordinary case today: no anchors exist, and the miner STILL loses the slice.
    Under the old self-gating behaviour it kept all of it."""
    miner, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                     anchor_outputs=None, escrow_address=ESCROW)
    assert carve == TOTAL * 25 // 100
    assert miner == MINER_IN - carve
    assert outs == [(ESCROW, carve)]


def test_a_claiming_provider_is_paid_and_the_remainder_escrows():
    claim = [(PROVIDER, 1_000_000_000)]
    miner, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                     anchor_outputs=claim, escrow_address=ESCROW)
    assert (PROVIDER, 1_000_000_000) in outs
    assert dict(outs)[ESCROW] == carve - 1_000_000_000
    assert miner == MINER_IN - carve


def test_emission_is_conserved_however_the_carve_is_split():
    for claim in (None, [], [(PROVIDER, 1)], [(PROVIDER, TOTAL * 25 // 100)],
                  [(PROVIDER, 5), (b"\x55" * 32, 7)]):
        miner, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                         anchor_outputs=claim, escrow_address=ESCROW)
        assert sum(a for _, a in outs) == carve, claim
        assert miner + carve == MINER_IN, claim


def test_anchors_can_never_draw_more_than_was_withheld():
    """Without the clamp a mis-scaled anchor set would pay out more than was subtracted
    from the miner — minting coin."""
    greedy = [(PROVIDER, TOTAL)]           # asks for the entire subsidy
    miner, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                     anchor_outputs=greedy, escrow_address=ESCROW)
    assert sum(a for _, a in outs) == carve
    assert miner == MINER_IN - carve
    assert ESCROW not in dict(outs), "a fully-claimed carve leaves no residual"


def test_a_zero_percent_carve_is_a_no_op():
    miner, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=0,
                                     anchor_outputs=None, escrow_address=ESCROW)
    assert (miner, outs, carve) == (MINER_IN, [], 0)


def test_a_zero_subsidy_block_carves_nothing():
    miner, outs, carve = split_carve(miner_reward=0, total_subsidy=0, pct=25,
                                     anchor_outputs=None, escrow_address=ESCROW)
    assert (miner, outs, carve) == (0, [], 0)


def test_integer_floor_never_rounds_up():
    """Rounding up would mint a nanoANM on some blocks."""
    for reward in (1, 19, 99, 101, 12_345_678_901):
        assert carve_amount(reward, 5) == reward * 5 // 100
        assert carve_amount(reward, 5) <= reward


def test_a_100_percent_carve_is_refused_rather_than_zeroing_a_miner():
    with pytest.raises(ServiceCarveError):
        carve_amount(TOTAL, 100)


def test_non_positive_anchor_amounts_are_ignored():
    miner, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                     anchor_outputs=[(PROVIDER, 0), (PROVIDER, -5)],
                                     escrow_address=ESCROW)
    assert outs == [(ESCROW, carve)]
    assert miner == MINER_IN - carve


# --------------------------------------------------------------------------- #
# The operator's stated split: 50% miner / 25% treasury / 25% inference        #
# --------------------------------------------------------------------------- #

def test_the_block_splits_exactly_50_25_25():
    """Stated requirement: 25% of every block reserved for inference, SEPARATE from the
    25% the treasury already receives. The remaining 50% is the miner's."""
    miner, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL,
                                     pct=25, anchor_outputs=None, escrow_address=ESCROW)
    assert TREASURY == TOTAL * 25 // 100
    assert carve == TOTAL * 25 // 100
    assert miner == TOTAL * 50 // 100
    assert miner + TREASURY + carve == TOTAL, "the block must be fully allocated"


def test_the_carve_is_measured_against_the_block_not_the_leftover():
    """The easy and expensive mistake: 25% of the POST-treasury 225 ANM would be
    56.25 ANM = 18.75% of the block, not the 75 ANM that was asked for."""
    _, _, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                              anchor_outputs=None, escrow_address=ESCROW)
    assert carve == 75_000_000_000
    assert carve != MINER_IN * 25 // 100, "must not be a share of the remainder"


def test_the_entire_slice_is_paid_out_every_block():
    """"It must be paid entirely each block" — nothing is ever withheld or returned to
    the miner, whatever providers claim."""
    for claim in (None, [], [(PROVIDER, 1)], [(PROVIDER, 30_000_000_000)],
                  [(PROVIDER, 75_000_000_000)]):
        miner, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL,
                                         pct=25, anchor_outputs=claim,
                                         escrow_address=ESCROW)
        assert sum(a for _, a in outs) == carve, f"slice not fully paid for {claim}"
        assert miner == MINER_IN - carve, "the miner never gets any of it back"


def test_with_no_inference_requests_the_whole_slice_goes_to_the_treasury():
    """Stated requirement. ESCROW is the foundation treasury address, so while nothing is
    claimed the treasury receives 25% + 25% = 50% of every block."""
    from consensus.rewards import FOUNDATION_TREASURY_ADDRESS

    assert ESCROW == address_to_bytes(FOUNDATION_TREASURY_ADDRESS)
    _, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                 anchor_outputs=None, escrow_address=ESCROW)
    assert outs == [(ESCROW, carve)]
    assert TREASURY + carve == TOTAL * 50 // 100


def test_the_carve_can_never_drive_the_miner_negative():
    """A carve larger than the miner slice would mean negative issuance. It cannot happen
    with 25/75, but the guard must exist because nothing else would catch it."""
    with pytest.raises(ServiceCarveError):
        split_carve(miner_reward=10, total_subsidy=TOTAL, pct=25,
                    anchor_outputs=None, escrow_address=ESCROW)


def test_conservation_holds_across_the_halving_schedule():
    """The 50/25/25 ratios must survive every epoch, including the dust at the tail."""
    for total in (TOTAL, TOTAL // 2, TOTAL // 4, 1_000_000, 400, 41, 7, 3):
        treasury = total * 25 // 100
        miner_in = total - treasury
        miner, outs, carve = split_carve(miner_reward=miner_in, total_subsidy=total,
                                         pct=25, anchor_outputs=None,
                                         escrow_address=ESCROW)
        assert sum(a for _, a in outs) == carve, total
        assert miner + treasury + carve == total, f"emission leaked at total={total}"
        assert miner >= 0 and carve >= 0
