"""
Tests for execution.state.aicf_state
"""

import pytest

from execution.state.aicf_state import (
    add_credits,
    add_governance_topup,
    add_inflow,
    compute_claimable,
    compute_epoch,
    distribute_epoch_to_miners,
    finalize_epoch,
    get_budget,
    get_credits_total,
    get_credits_user,
    get_epoch_length,
    get_epoch_miners,
    get_inflow,
    get_last_claimed_epoch,
    get_pool_balance,
    process_claim,
    safe_add,
    safe_mul_div,
    set_epoch_length,
)


class MockState:
    """Mock state for testing AICF state operations."""
    
    def __init__(self):
        self.data = {}
    
    def get(self, key: str):
        return self.data.get(key)
    
    def put(self, key: str, value):
        self.data[key] = value


def test_compute_epoch():
    assert compute_epoch(0, 100) == 0
    assert compute_epoch(99, 100) == 0
    assert compute_epoch(100, 100) == 1
    assert compute_epoch(199, 100) == 1
    assert compute_epoch(200, 100) == 2


def test_safe_add():
    assert safe_add(100, 200) == 300
    assert safe_add(0, 0) == 0
    
    # Test overflow
    with pytest.raises(OverflowError):
        safe_add(2**256 - 1, 1)


def test_safe_mul_div():
    assert safe_mul_div(1000, 5000, 10000) == 500
    assert safe_mul_div(1000, 0, 10000) == 0
    assert safe_mul_div(1000, 10000, 10000) == 1000
    
    # Division by zero
    assert safe_mul_div(1000, 5000, 0) == 0


def test_epoch_length():
    state = MockState()
    
    # Default
    assert get_epoch_length(state) == 100
    
    # Set and get
    set_epoch_length(state, 200)
    assert get_epoch_length(state) == 200


def test_add_credits():
    state = MockState()
    miner = b"\x01" * 32
    
    # Add credits to epoch 0
    add_credits(state, 0, miner, 1_000_000)
    
    assert get_credits_user(state, 0, miner) == 1_000_000
    assert get_credits_total(state, 0) == 1_000_000
    
    # Add more credits
    add_credits(state, 0, miner, 500_000)
    
    assert get_credits_user(state, 0, miner) == 1_500_000
    assert get_credits_total(state, 0) == 1_500_000
    
    # Different miner
    miner2 = b"\x02" * 32
    add_credits(state, 0, miner2, 1_000_000)
    
    assert get_credits_user(state, 0, miner2) == 1_000_000
    assert get_credits_total(state, 0) == 2_500_000


def test_add_inflow():
    state = MockState()
    
    # Add inflow to epoch 0
    add_inflow(state, 0, 1_000_000_000)
    
    assert get_inflow(state, 0) == 1_000_000_000
    assert get_pool_balance(state) == 1_000_000_000
    
    # Add more inflow
    add_inflow(state, 0, 500_000_000)
    
    assert get_inflow(state, 0) == 1_500_000_000
    assert get_pool_balance(state) == 1_500_000_000
    
    # Different epoch
    add_inflow(state, 1, 1_000_000_000)
    
    assert get_inflow(state, 1) == 1_000_000_000
    assert get_pool_balance(state) == 2_500_000_000


def test_finalize_epoch():
    state = MockState()
    
    # Add inflow to epoch 0
    add_inflow(state, 0, 1_000_000_000)
    
    # Finalize epoch 0 with 50% payout
    budget = finalize_epoch(state, 0, epoch_payout_bps=5000)
    
    assert budget == 500_000_000
    assert get_budget(state, 0) == 500_000_000
    
    # Pool balance should still be 1B (budget just marks it for distribution)
    assert get_pool_balance(state) == 1_000_000_000


def test_finalize_epoch_caps_by_pool():
    state = MockState()
    
    # Add small inflow
    add_inflow(state, 0, 100_000_000)
    
    # Manually set pool to lower value (simulate previous distributions)
    state.put("aicf.pool_balance", 50_000_000)
    
    # Finalize with 100% payout - should be capped by pool
    budget = finalize_epoch(state, 0, epoch_payout_bps=10_000)
    
    assert budget == 50_000_000  # Capped by pool balance


def test_compute_claimable_no_claims():
    state = MockState()
    user = b"\x01" * 32
    
    # No epochs finalized yet
    claimable = compute_claimable(state, user, current_epoch=0)
    
    assert claimable.total_claimable == 0
    assert claimable.epochs == []


def test_compute_claimable_single_epoch():
    state = MockState()
    user = b"\x01" * 32
    
    # Epoch 0: Add credits and budget
    add_credits(state, 0, user, 1_000_000)
    add_inflow(state, 0, 1_000_000_000)
    finalize_epoch(state, 0, epoch_payout_bps=5000)
    
    # In epoch 2, epoch 0 is claimable (current - 2)
    claimable = compute_claimable(state, user, current_epoch=2)
    
    # User has 100% of credits, so gets 100% of budget
    assert claimable.total_claimable == 500_000_000
    assert claimable.epochs == [0]


def test_compute_claimable_multiple_users():
    state = MockState()
    user1 = b"\x01" * 32
    user2 = b"\x02" * 32
    
    # Epoch 0: Two users with different credits
    add_credits(state, 0, user1, 1_000_000)  # 1M credits
    add_credits(state, 0, user2, 3_000_000)  # 3M credits (75% of total)
    add_inflow(state, 0, 1_000_000_000)
    finalize_epoch(state, 0, epoch_payout_bps=5000)  # 500M budget
    
    # In epoch 2, epoch 0 is claimable
    claimable1 = compute_claimable(state, user1, current_epoch=2)
    claimable2 = compute_claimable(state, user2, current_epoch=2)
    
    # User1: 1M / 4M * 500M = 125M
    assert claimable1.total_claimable == 125_000_000
    
    # User2: 3M / 4M * 500M = 375M
    assert claimable2.total_claimable == 375_000_000


def test_compute_claimable_multiple_epochs():
    state = MockState()
    user = b"\x01" * 32
    
    # Epoch 0
    add_credits(state, 0, user, 1_000_000)
    add_inflow(state, 0, 1_000_000_000)
    finalize_epoch(state, 0, epoch_payout_bps=5000)  # 500M
    
    # Epoch 1
    add_credits(state, 1, user, 2_000_000)
    add_inflow(state, 1, 2_000_000_000)
    finalize_epoch(state, 1, epoch_payout_bps=5000)  # 1B
    
    # In epoch 3, epochs 0 and 1 are claimable
    claimable = compute_claimable(state, user, current_epoch=3)
    
    # Total: 500M + 1B = 1.5B
    assert claimable.total_claimable == 1_500_000_000
    assert claimable.epochs == [0, 1]


def test_compute_claimable_respects_max_epochs():
    state = MockState()
    user = b"\x01" * 32
    
    # Create many epochs
    for epoch in range(10):
        add_credits(state, epoch, user, 1_000_000)
        add_inflow(state, epoch, 1_000_000_000)
        finalize_epoch(state, epoch, epoch_payout_bps=5000)
    
    # Compute claimable with max_epochs=3
    claimable = compute_claimable(state, user, current_epoch=12, max_epochs=3)
    
    # Should only claim 3 epochs (0, 1, 2)
    assert len(claimable.epochs) == 3
    assert claimable.epochs == [0, 1, 2]


def test_process_claim():
    state = MockState()
    user = b"\x01" * 32
    
    # Setup epoch 0
    add_credits(state, 0, user, 1_000_000)
    add_inflow(state, 0, 1_000_000_000)
    finalize_epoch(state, 0, epoch_payout_bps=5000)  # 500M
    
    # Process claim in epoch 2
    amount, epochs = process_claim(state, user, current_epoch=2)
    
    assert amount == 500_000_000
    assert epochs == [0]
    assert get_last_claimed_epoch(state, user) == 0
    assert get_pool_balance(state) == 500_000_000  # 1B - 500M
    
    # Second claim should return 0 (idempotent)
    amount2, epochs2 = process_claim(state, user, current_epoch=2)
    
    assert amount2 == 0
    assert epochs2 == []


def test_process_claim_insufficient_pool():
    state = MockState()
    user = b"\x01" * 32
    
    # Setup epoch 0
    add_credits(state, 0, user, 1_000_000)
    add_inflow(state, 0, 1_000_000_000)
    finalize_epoch(state, 0, epoch_payout_bps=5000)
    
    # Manually drain pool to simulate error condition
    state.put("aicf.pool_balance", 100_000_000)  # Less than claimable
    
    # Should raise error
    with pytest.raises(RuntimeError, match="Insufficient AICF pool balance"):
        process_claim(state, user, current_epoch=2)


def test_add_governance_topup():
    state = MockState()
    
    # Add topup to epoch 0
    add_governance_topup(state, current_epoch=0, amount=5_000_000_000)
    
    assert get_inflow(state, 0) == 5_000_000_000
    assert get_pool_balance(state) == 5_000_000_000


def test_compute_claimable_skip_zero_budget_epochs():
    state = MockState()
    user = b"\x01" * 32
    
    # Epoch 0: Has budget
    add_credits(state, 0, user, 1_000_000)
    add_inflow(state, 0, 1_000_000_000)
    finalize_epoch(state, 0, epoch_payout_bps=5000)
    
    # Epoch 1: Has credits but no budget
    add_credits(state, 1, user, 1_000_000)
    # No inflow/budget
    
    # Epoch 2: Has budget
    add_credits(state, 2, user, 1_000_000)
    add_inflow(state, 2, 1_000_000_000)
    finalize_epoch(state, 2, epoch_payout_bps=5000)
    
    # In epoch 4, should only claim epochs 0 and 2
    claimable = compute_claimable(state, user, current_epoch=4)
    
    assert claimable.epochs == [0, 2]
    assert claimable.total_claimable == 1_000_000_000  # 500M + 500M


def test_compute_claimable_skip_zero_credits_epochs():
    state = MockState()
    user = b"\x01" * 32
    
    # Epoch 0: User has credits
    add_credits(state, 0, user, 1_000_000)
    add_inflow(state, 0, 1_000_000_000)
    finalize_epoch(state, 0, epoch_payout_bps=5000)
    
    # Epoch 1: User has no credits (different miner got them)
    other_user = b"\x02" * 32
    add_credits(state, 1, other_user, 1_000_000)
    add_inflow(state, 1, 1_000_000_000)
    finalize_epoch(state, 1, epoch_payout_bps=5000)
    
    # In epoch 3, user should only claim epoch 0
    claimable = compute_claimable(state, user, current_epoch=3)
    
    assert claimable.epochs == [0]
    assert claimable.total_claimable == 500_000_000


def test_get_epoch_miners_tracks_recipients():
    state = MockState()
    a = b"\x01" * 32
    b = b"\x02" * 32
    add_credits(state, 7, a, 1_000)
    add_credits(state, 7, b, 2_000)
    add_credits(state, 7, a, 500)  # duplicate insert must be idempotent
    miners = get_epoch_miners(state, 7)
    assert sorted(miners) == sorted([a, b])
    # Sorted deterministically across calls
    assert miners == sorted(miners)


def test_distribute_epoch_to_miners_credits_pro_rata():
    state = MockState()
    a = b"\x01" * 32
    b = b"\x02" * 32

    # Two miners earn credits 3:1 in epoch 0; AI usage drives 1_000_000 nANM
    # of inflows; 50% epoch_payout_bps gives 500_000 distributable budget.
    add_credits(state, 0, a, 3_000)
    add_credits(state, 0, b, 1_000)
    add_inflow(state, 0, 1_000_000)
    finalize_epoch(state, 0, epoch_payout_bps=5000)

    paid: list[tuple[bytes, int]] = []

    def fake_credit(address: bytes, amount: int) -> None:
        paid.append((bytes(address), int(amount)))

    payouts = distribute_epoch_to_miners(state, 0, credit_balance=fake_credit)
    assert len(payouts) == 2
    by_addr = {addr: amt for addr, amt in payouts}
    # 500_000 * 3 / 4 = 375_000 ; 500_000 * 1 / 4 = 125_000
    assert by_addr[a] == 375_000
    assert by_addr[b] == 125_000
    # The pool's virtual counter has been debited by the same total.
    assert get_pool_balance(state) == 1_000_000 - 500_000
    # And last_claimed_epoch is bumped so a second pass is a no-op.
    assert get_last_claimed_epoch(state, a) == 0
    assert get_last_claimed_epoch(state, b) == 0
    paid.clear()
    again = distribute_epoch_to_miners(state, 0, credit_balance=fake_credit)
    assert again == [] and paid == []


def test_distribute_epoch_skips_miners_who_already_claimed():
    state = MockState()
    a = b"\x01" * 32
    add_credits(state, 0, a, 1_000)
    add_inflow(state, 0, 1_000_000)
    finalize_epoch(state, 0, epoch_payout_bps=5000)
    # Simulate a manual claim TX bumping the cursor.
    process_claim(state, a, current_epoch=2)
    paid: list[tuple[bytes, int]] = []
    distribute_epoch_to_miners(
        state, 0, credit_balance=lambda addr, amt: paid.append((addr, amt))
    )
    assert paid == []
