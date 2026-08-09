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
    FOUNDATION_TREASURY_ADDRESS,
    SERVICE_CARVE_PCT_V1,
    SERVICE_ESCROW_ADDRESS,
    service_carve_pct,
)
from consensus.service_carve import ServiceCarveError, carve_amount, split_carve
from core.utils.address import address_to_bytes

BELOW, AT = 74_999, 75_000
ESCROW = address_to_bytes(SERVICE_ESCROW_ADDRESS)
TREASURY_ADDR = address_to_bytes(FOUNDATION_TREASURY_ADDRESS)
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


def test_the_escrow_address_uses_the_only_spendable_signature_scheme():
    """The escrow accrues 25% of every block forever, so it must be spendable.

    ml_dsa_65 (0x1003) is the only scheme on this chain whose signatures are actually
    verified; 0x1002 is a forgeable commitment stub and sphincs_shake_128s addresses at
    this account length are the stranded class. A typo in the literal still decodes as
    valid bech32m, so checking the scheme id is what catches a hand-edited address before
    it silently swallows the slice.
    """
    from pq.py.address import decode_address

    rec = decode_address(SERVICE_ESCROW_ADDRESS)
    assert rec.alg_id == 0x1003, f"escrow must be ml_dsa_65, got {hex(rec.alg_id)}"


def test_the_escrow_is_a_dedicated_address_not_the_treasury():
    """The escrow exists to be independently auditable.

    If the residual landed in the foundation treasury the balance could not distinguish
    "the slice reached providers" from "the slice piled up unclaimed", which is precisely
    the question this fork exists to answer. Keep them distinct.
    """
    from consensus.rewards import FOUNDATION_TREASURY_ADDRESS

    assert SERVICE_ESCROW_ADDRESS != FOUNDATION_TREASURY_ADDRESS
    assert ESCROW != address_to_bytes(FOUNDATION_TREASURY_ADDRESS)


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
    """Stated requirement, verbatim: "if there is no inference request at all it goes to
    the treasury". It is still withheld from the miner in full — it does not fall back
    into the coinbase, which is the behaviour this fork exists to remove.

    Nothing is owed to any provider in this block, so the slice is operator revenue and
    must NOT sit in escrow. Combined with the treasury's own separate 25%, the operator
    receives 50% of such a block.
    """
    _, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                 anchor_outputs=None, escrow_address=ESCROW,
                                 treasury_address=TREASURY_ADDR)
    assert outs == [(TREASURY_ADDR, carve)]
    assert ESCROW not in dict(outs), "nothing is owed, so nothing may sit in escrow"
    assert carve == TOTAL * 25 // 100
    assert TREASURY + carve == TOTAL * 50 // 100, "operator take: its own 25% plus the slice"


def test_a_partially_claimed_slice_holds_the_remainder_in_escrow():
    """When providers DID settle but did not consume the slice, the remainder is owed to
    providers rather than earned by the operator, so it holds at the dedicated escrow.

    This is the branch that makes the escrow balance meaningful: it can only rise when
    inference actually happened, so it never has to be disentangled from treasury flow.
    """
    claim = [(PROVIDER, 1_000_000_000)]  # 1 ANM of a 75 ANM slice
    _, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                 anchor_outputs=claim, escrow_address=ESCROW,
                                 treasury_address=TREASURY_ADDR)
    by_addr = dict(outs)
    assert by_addr[PROVIDER] == 1_000_000_000
    assert by_addr[ESCROW] == carve - 1_000_000_000
    assert TREASURY_ADDR not in by_addr, "the treasury does not take what providers are owed"
    assert sum(a for _, a in outs) == carve


def test_the_two_residual_destinations_are_actually_different_accounts():
    """The routing above is only meaningful if the accounts differ — the whole point of a
    dedicated escrow. A regression to escrow == treasury would make both tests above pass
    vacuously, so assert the distinction directly."""
    assert ESCROW != TREASURY_ADDR
    _, no_claim, _ = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                 anchor_outputs=None, escrow_address=ESCROW,
                                 treasury_address=TREASURY_ADDR)
    _, partial, _ = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                anchor_outputs=[(PROVIDER, 1)], escrow_address=ESCROW,
                                treasury_address=TREASURY_ADDR)
    assert no_claim[0][0] == TREASURY_ADDR
    assert dict(partial)[ESCROW] > 0


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


# --------------------------------------------------------------------------- #
# The cap interaction — the bug a pure-function test could never see           #
# --------------------------------------------------------------------------- #
#
# Every test above calls split_carve directly, so none of them saw the settlement
# pool cap that block_import applied on the way in. That cap is 50 ANM scaled by
# subsidy/300 == total/6 == 16.67%, which is SMALLER than the 25% carve at every
# height — so min(cap, carve) always picked the cap and 8.33% of every block was
# withheld from the miner while being structurally unreachable by any provider.
# "Paid entirely each block" was silently false. These tests pin the arithmetic that
# makes it true.

def _params():
    from consensus.tests.test_foundation_split import MAINNET, _load_full_params_dict

    return _load_full_params_dict(MAINNET)


def test_the_settlement_pool_cap_is_smaller_than_the_carve_at_every_height():
    """The fact that made the bug invisible: the cap ALWAYS wins a min() against the
    carve, so applying both can only ever throttle the slice."""
    from consensus.iou_settlement import settlement_pool_cap
    from consensus.rewards import _subsidy_total_for_height, parse_emission_schedule

    params = _params()
    sched = parse_emission_schedule(params)
    for h in (75_001, 1_350_001, 2_700_001, 4_050_001):
        total = _subsidy_total_for_height(h, sched)
        cap = settlement_pool_cap(h, params)
        carve = total * 25 // 100
        assert cap < carve, (
            f"at height {h} the pool cap {cap} is not below the carve {carve}; if this "
            f"ever flips, re-check whether block_import should bound anchors by the cap"
        )


def test_a_provider_can_claim_the_entire_slice():
    """With the cap removed the carve is the budget, so a provider claiming the whole
    25% receives all of it and nothing escrows. Under the old min(cap, carve) the most
    reachable was 16.67% of the block."""
    whole = TOTAL * 25 // 100
    miner, outs, carve = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                     anchor_outputs=[(PROVIDER, whole)],
                                     escrow_address=ESCROW)
    assert carve == whole
    assert dict(outs).get(PROVIDER) == whole, "a provider must be able to reach 25%"
    assert ESCROW not in dict(outs), "nothing should escrow when the slice is fully claimed"
    assert miner == MINER_IN - carve


def test_no_part_of_the_slice_is_structurally_unreachable():
    """For claims from 1 nanoANM up to the whole carve, the amount reaching providers
    tracks the claim exactly — there is no ceiling below the carve."""
    carve = TOTAL * 25 // 100
    for claim in (1, carve // 4, carve // 2, carve - 1, carve):
        _, outs, c = split_carve(miner_reward=MINER_IN, total_subsidy=TOTAL, pct=25,
                                 anchor_outputs=[(PROVIDER, claim)],
                                 escrow_address=ESCROW)
        assert dict(outs).get(PROVIDER, 0) == claim, claim
        assert sum(a for _, a in outs) == c == carve
