"""
Tests for Phase 2 State Management
====================================

Unit tests for provider registry, receipt storage, and payout accounting.
"""

import pytest
from execution.state.phase2_state import (
    # Provider functions
    is_provider_registered,
    register_provider,
    get_provider_stake,
    get_provider_payout_address,
    update_provider_heartbeat,
    record_provider_job_success,
    record_provider_job_failure,
    # Receipt functions
    store_receipt,
    get_receipt_data,
    mark_receipt_matured,
    # Payout functions
    compute_epoch,
    get_maturity_config,
    set_maturity_config,
    add_receipt_to_provider_accrual,
    finalize_provider_epoch,
    get_provider_claimable,
    process_provider_claim,
    add_epoch_inflow,
    finalize_epoch,
    # Training functions
    store_training_receipt,
    mark_training_receipt_verified,
    # Helpers
    safe_add,
    safe_sub,
    safe_mul_div,
)


class MockState:
    """Mock state object for testing."""
    
    def __init__(self):
        self.data = {}
    
    def get(self, key: str):
        return self.data.get(key)
    
    def put(self, key: str, value):
        self.data[key] = value


# ============================================================================
# Provider Registry Tests
# ============================================================================

def test_register_provider_basic():
    """Test basic provider registration."""
    state = MockState()
    address = bytes.fromhex("aa" * 32)
    payout_addr = bytes.fromhex("bb" * 32)
    
    # Initially not registered
    assert not is_provider_registered(state, address)
    
    # Register
    register_provider(
        state,
        address=address,
        payout_addr=payout_addr,
        stake=1000000000,  # 1 ANM
        bond=500000000,  # 0.5 ANM
        capabilities='{"model_family":"nvidia-h100"}',
        timestamp=1234567890,
    )
    
    # Now registered
    assert is_provider_registered(state, address)
    assert get_provider_stake(state, address) == 1000000000
    assert get_provider_payout_address(state, address) == payout_addr


def test_register_provider_duplicate():
    """Test that duplicate registration fails."""
    state = MockState()
    address = bytes.fromhex("aa" * 32)
    
    register_provider(
        state,
        address=address,
        payout_addr=None,
        stake=1000,
        bond=100,
        capabilities='{}',
        timestamp=123,
    )
    
    # Second registration should fail
    with pytest.raises(ValueError, match="already registered"):
        register_provider(
            state,
            address=address,
            payout_addr=None,
            stake=2000,
            bond=200,
            capabilities='{}',
            timestamp=456,
        )


def test_provider_heartbeat():
    """Test provider heartbeat updates."""
    state = MockState()
    address = bytes.fromhex("aa" * 32)
    
    register_provider(
        state,
        address=address,
        payout_addr=None,
        stake=1000,
        bond=100,
        capabilities='{}',
        timestamp=100,
    )
    
    # Update heartbeat
    update_provider_heartbeat(state, address, 200)
    
    # Verify stored
    key = f"phase2.provider.{'aa' * 32}.last_heartbeat"
    assert state.get(key) == 200


def test_provider_reputation_tracking():
    """Test provider reputation counters."""
    state = MockState()
    address = bytes.fromhex("aa" * 32)
    
    register_provider(
        state,
        address=address,
        payout_addr=None,
        stake=1000,
        bond=100,
        capabilities='{}',
        timestamp=100,
    )
    
    # Record successes
    record_provider_job_success(state, address, 100)
    record_provider_job_success(state, address, 200)
    
    # Record failure
    record_provider_job_failure(state, address)
    
    # Verify counters
    addr_hex = address.hex()
    success_key = f"phase2.provider.{addr_hex}.reputation.successful"
    failed_key = f"phase2.provider.{addr_hex}.reputation.failed"
    tokens_key = f"phase2.provider.{addr_hex}.reputation.total_tokens"
    
    assert state.get(success_key) == 2
    assert state.get(failed_key) == 1
    assert state.get(tokens_key) == 300


# ============================================================================
# Receipt Storage Tests
# ============================================================================

def test_store_receipt():
    """Test receipt storage and retrieval."""
    state = MockState()
    receipt_hash = bytes.fromhex("cc" * 32)
    receipt_data = b"test_receipt_data"
    provider_addr = bytes.fromhex("dd" * 32)
    
    store_receipt(
        state,
        receipt_hash=receipt_hash,
        receipt_data=receipt_data,
        provider_address=provider_addr,
        height=1000,
        timestamp=123456,
    )
    
    # Retrieve receipt
    retrieved = get_receipt_data(state, receipt_hash)
    assert retrieved == receipt_data


def test_mark_receipt_matured():
    """Test marking receipt as matured."""
    state = MockState()
    receipt_hash = bytes.fromhex("cc" * 32)
    
    store_receipt(
        state,
        receipt_hash=receipt_hash,
        receipt_data=b"data",
        provider_address=bytes.fromhex("dd" * 32),
        height=1000,
        timestamp=123,
    )
    
    # Initially pending
    status_key = f"phase2.receipt.{'cc' * 32}.status"
    assert state.get(status_key) == "pending"
    
    # Mark matured
    mark_receipt_matured(state, receipt_hash)
    assert state.get(status_key) == "matured"


# ============================================================================
# Payout Accounting Tests
# ============================================================================

def test_compute_epoch():
    """Test epoch computation."""
    assert compute_epoch(0, 100) == 0
    assert compute_epoch(99, 100) == 0
    assert compute_epoch(100, 100) == 1
    assert compute_epoch(199, 100) == 1
    assert compute_epoch(1000, 100) == 10


def test_maturity_config():
    """Test maturity configuration get/set."""
    state = MockState()
    
    # Set config
    set_maturity_config(state, maturity_depth=50, epoch_length=100, reserve_ratio_bps=1000)
    
    # Get config
    depth, epoch_len, reserve_bps = get_maturity_config(state)
    assert depth == 50
    assert epoch_len == 100
    assert reserve_bps == 1000


def test_provider_accrual():
    """Test provider accrual tracking."""
    state = MockState()
    provider_addr = bytes.fromhex("ee" * 32)
    
    # Add receipts to epoch 0
    add_receipt_to_provider_accrual(
        state,
        provider_address=provider_addr,
        epoch=0,
        provider_cut=1000,
        tokens_processed=100,
    )
    
    add_receipt_to_provider_accrual(
        state,
        provider_address=provider_addr,
        epoch=0,
        provider_cut=2000,
        tokens_processed=200,
    )
    
    # Verify totals
    addr_hex = provider_addr.hex()
    accrued_key = f"phase2.payout.{addr_hex}.epoch.0.accrued"
    receipts_key = f"phase2.payout.{addr_hex}.epoch.0.receipt_count"
    tokens_key = f"phase2.payout.{addr_hex}.epoch.0.tokens_processed"
    
    assert state.get(accrued_key) == 3000
    assert state.get(receipts_key) == 2
    assert state.get(tokens_key) == 300


def test_finalize_provider_epoch():
    """Test provider epoch finalization."""
    state = MockState()
    provider_addr = bytes.fromhex("ee" * 32)
    
    # Add accrual
    add_receipt_to_provider_accrual(
        state,
        provider_address=provider_addr,
        epoch=0,
        provider_cut=5000,
        tokens_processed=100,
    )
    
    # Finalize
    accrued = finalize_provider_epoch(state, provider_addr, epoch=0, height=100)
    assert accrued == 5000
    
    # Verify finalized flag
    addr_hex = provider_addr.hex()
    fin_key = f"phase2.payout.{addr_hex}.epoch.0.finalized"
    assert state.get(fin_key) == 100


def test_get_provider_claimable():
    """Test claimable calculation across epochs."""
    state = MockState()
    provider_addr = bytes.fromhex("ee" * 32)
    
    # Epoch 0: accrued 1000, claimed 0
    add_receipt_to_provider_accrual(state, provider_addr, 0, 1000, 100)
    finalize_provider_epoch(state, provider_addr, 0, 100)
    
    # Epoch 1: accrued 2000, claimed 0
    add_receipt_to_provider_accrual(state, provider_addr, 1, 2000, 200)
    finalize_provider_epoch(state, provider_addr, 1, 200)
    
    # Epoch 2: not finalized
    add_receipt_to_provider_accrual(state, provider_addr, 2, 3000, 300)
    
    # Get claimable
    total, valid_epochs = get_provider_claimable(state, provider_addr, [0, 1, 2])
    
    assert total == 3000  # 1000 + 2000 (epoch 2 not finalized)
    assert valid_epochs == [0, 1]


def test_process_provider_claim():
    """Test provider claim processing."""
    state = MockState()
    provider_addr = bytes.fromhex("ee" * 32)
    claim_id = bytes.fromhex("ff" * 32)
    
    # Setup: accrued in epochs 0 and 1
    add_receipt_to_provider_accrual(state, provider_addr, 0, 1000, 100)
    finalize_provider_epoch(state, provider_addr, 0, 100)
    
    add_receipt_to_provider_accrual(state, provider_addr, 1, 2000, 200)
    finalize_provider_epoch(state, provider_addr, 1, 200)
    
    # Claim 2500 (should take all from epoch 0 and 1500 from epoch 1)
    process_provider_claim(
        state,
        provider_address=provider_addr,
        epochs=[0, 1],
        amount=2500,
        claim_id=claim_id,
        height=300,
        timestamp=123456,
    )
    
    # Verify claimed amounts
    addr_hex = provider_addr.hex()
    claimed0_key = f"phase2.payout.{addr_hex}.epoch.0.claimed"
    claimed1_key = f"phase2.payout.{addr_hex}.epoch.1.claimed"
    
    assert state.get(claimed0_key) == 1000
    assert state.get(claimed1_key) == 1500
    
    # Verify remaining claimable
    total, _ = get_provider_claimable(state, provider_addr, [0, 1])
    assert total == 500  # 2000 - 1500 from epoch 1


def test_process_claim_exceeds_claimable():
    """Test that claiming more than available fails."""
    state = MockState()
    provider_addr = bytes.fromhex("ee" * 32)
    claim_id = bytes.fromhex("ff" * 32)
    
    add_receipt_to_provider_accrual(state, provider_addr, 0, 1000, 100)
    finalize_provider_epoch(state, provider_addr, 0, 100)
    
    # Try to claim more than available
    with pytest.raises(ValueError, match="only 1000 available"):
        process_provider_claim(
            state,
            provider_address=provider_addr,
            epochs=[0],
            amount=2000,
            claim_id=claim_id,
            height=200,
            timestamp=123,
        )


def test_epoch_pool_accounting():
    """Test epoch-level pool accounting."""
    state = MockState()
    
    # Add inflows
    add_epoch_inflow(state, epoch=0, amount=10000)
    add_epoch_inflow(state, epoch=0, amount=5000)
    
    # Finalize with 10% reserve
    reserve = finalize_epoch(state, epoch=0, height=100, reserve_ratio_bps=1000)
    
    # Reserve should be 10% of 15000 = 1500
    assert reserve == 1500
    
    # Verify finalized flag
    fin_key = "phase2.payout.epoch.0.finalized"
    assert state.get(fin_key) == "1"


# ============================================================================
# Training Receipt Tests
# ============================================================================

def test_store_training_receipt():
    """Test training receipt storage."""
    state = MockState()
    receipt_hash = bytes.fromhex("aa" * 32)
    receipt_data = b"training_receipt_data"
    miner_addr = bytes.fromhex("bb" * 32)
    
    store_training_receipt(
        state,
        receipt_hash=receipt_hash,
        receipt_data=receipt_data,
        miner_address=miner_addr,
        training_credit=5000,
        height=1000,
        timestamp=123456,
    )
    
    # Verify stored
    receipt_hex = receipt_hash.hex()
    data_key = f"phase2.training.{receipt_hex}.data"
    miner_key = f"phase2.training.{receipt_hex}.miner"
    credit_key = f"phase2.training.{receipt_hex}.credit"
    
    assert bytes.fromhex(state.get(data_key)) == receipt_data
    assert state.get(miner_key) == miner_addr.hex()
    assert state.get(credit_key) == 5000


def test_mark_training_receipt_verified():
    """Test marking training receipt as verified."""
    state = MockState()
    receipt_hash = bytes.fromhex("aa" * 32)
    
    store_training_receipt(
        state,
        receipt_hash=receipt_hash,
        receipt_data=b"data",
        miner_address=bytes.fromhex("bb" * 32),
        training_credit=1000,
        height=100,
        timestamp=123,
    )
    
    # Initially not verified
    receipt_hex = receipt_hash.hex()
    verified_key = f"phase2.training.{receipt_hex}.verified"
    assert state.get(verified_key) == "0"
    
    # Mark verified
    mark_training_receipt_verified(state, receipt_hash)
    assert state.get(verified_key) == "1"


# ============================================================================
# Safe Arithmetic Tests
# ============================================================================

def test_safe_add_normal():
    """Test safe addition for normal values."""
    assert safe_add(100, 200) == 300
    assert safe_add(0, 0) == 0


def test_safe_add_overflow():
    """Test safe addition overflow detection."""
    max_val = (2 ** 256) - 1
    with pytest.raises(OverflowError):
        safe_add(max_val, 1)


def test_safe_sub_normal():
    """Test safe subtraction for normal values."""
    assert safe_sub(200, 100) == 100
    assert safe_sub(100, 100) == 0


def test_safe_sub_underflow():
    """Test safe subtraction underflow detection."""
    with pytest.raises(ValueError):
        safe_sub(100, 200)


def test_safe_mul_div_normal():
    """Test safe mul/div for normal values."""
    assert safe_mul_div(100, 200, 10) == 2000
    assert safe_mul_div(1000, 50, 100) == 500


def test_safe_mul_div_zero():
    """Test safe mul/div with zero divisor."""
    with pytest.raises(ZeroDivisionError):
        safe_mul_div(100, 200, 0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
