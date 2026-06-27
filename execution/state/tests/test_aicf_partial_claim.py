"""
Tests for execution.state.aicf_state.process_partial_claim — the claim flow used
by execution/runtime/aicf_claim.py (claim exact / claim too much / claim all).
"""

import pytest

from execution.state.aicf_state import (
    compute_claimable,
    get_pool_balance,
    process_partial_claim,
    set_budget,
    set_credits_total,
    set_credits_user,
    set_pool_balance,
)


class MockState:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value


ADDR = b"\x11" * 32
CURRENT_EPOCH = 3          # epochs 0 and 1 are finalized (< current_epoch - 1)
MIN_CLAIM = 1_000_000


def _seed():
    """Two finalized epochs (0,1), the address holds 100% of credits in each."""
    state = MockState()
    for epoch in (0, 1):
        set_credits_total(state, epoch, 1_000)
        set_credits_user(state, epoch, ADDR, 1_000)
        set_budget(state, epoch, 5_000_000)
    set_pool_balance(state, 100_000_000)
    return state


def test_claim_all():
    state = _seed()
    info = compute_claimable(state, ADDR, CURRENT_EPOCH)
    assert info.total_claimable > 0
    pool_before = get_pool_balance(state)

    paid, epochs = process_partial_claim(
        state, ADDR, amount=0, current_epoch=CURRENT_EPOCH,
        current_height=400, min_claim=MIN_CLAIM,
    )
    assert paid == info.total_claimable        # amount==0 claims everything
    assert set(epochs) == {0, 1}
    assert get_pool_balance(state) == pool_before - paid


def test_claim_exact_partial():
    state = _seed()
    info = compute_claimable(state, ADDR, CURRENT_EPOCH)
    first_share = info.details[0][3]           # (epoch, credits_user, credits_total, share)
    assert 0 < first_share < info.total_claimable

    paid, epochs = process_partial_claim(
        state, ADDR, amount=first_share, current_epoch=CURRENT_EPOCH,
        current_height=400, min_claim=MIN_CLAIM,
    )
    assert paid == first_share                 # claimed exactly the oldest epoch's share
    assert epochs == [info.details[0][0]]


def test_claim_too_much_raises():
    state = _seed()
    info = compute_claimable(state, ADDR, CURRENT_EPOCH)
    with pytest.raises(ValueError, match="exceeds available"):
        process_partial_claim(
            state, ADDR, amount=info.total_claimable + MIN_CLAIM,
            current_epoch=CURRENT_EPOCH, current_height=400, min_claim=MIN_CLAIM,
        )


def test_claim_below_minimum_raises():
    state = _seed()
    with pytest.raises(ValueError, match="below minimum"):
        process_partial_claim(
            state, ADDR, amount=1, current_epoch=CURRENT_EPOCH,
            current_height=400, min_claim=MIN_CLAIM,
        )


def test_claim_when_nothing_claimable_raises():
    state = MockState()
    set_pool_balance(state, 100_000_000)
    with pytest.raises(ValueError, match="No claimable"):
        process_partial_claim(
            state, ADDR, amount=0, current_epoch=CURRENT_EPOCH,
            current_height=400, min_claim=MIN_CLAIM,
        )
