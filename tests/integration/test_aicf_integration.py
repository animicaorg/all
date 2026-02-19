"""
Integration test for AICF block processing and credit awarding.

This test verifies:
1. Block reward slicing (miner vs AICF pool)
2. Fee routing to AICF pool
3. Credit awarding to miners
4. Epoch finalization at boundaries
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest


def test_aicf_block_processing_mock():
    """Test AICF processing with mock state."""
    # Mock state object
    class MockState:
        def __init__(self):
            self.data = {}
        
        def get(self, key, default=None):
            return self.data.get(key, default)
        
        def put(self, key, value):
            self.data[key] = value
        
        def compute_state_root(self):
            return b"\x00" * 32
    
    # Mock block environment
    class MockBlockEnv:
        def __init__(self):
            self.height = 100
            self.timestamp = 1234567890
            self.coinbase = b"\x01" * 32
    
    state = MockState()
    block_env = MockBlockEnv()
    miner_address = b"\x01" * 32
    
    # AICF parameters
    params = {
        "aicf": {
            "epoch_length_blocks": 100,
            "credits_per_block": 1_000_000,
            "epoch_payout_bps": 5000,
        }
    }
    
    # Initialize epoch length
    from execution.state.aicf_state import set_epoch_length
    set_epoch_length(state, 100)
    
    # Process block for AICF
    from execution.runtime.aicf_integration import process_block_for_aicf
    
    process_block_for_aicf(
        state=state,
        block_env=block_env,
        miner_address=miner_address,
        block_reward_aicf_amount=5_000_000_000,  # 5 ANM to AICF
        fee_aicf_amount=1_000_000_000,  # 1 ANM in fees to AICF
        params=params,
    )
    
    # Verify epoch length was set
    from execution.state.aicf_state import get_epoch_length
    assert get_epoch_length(state) == 100
    
    # Verify credits were awarded
    from execution.state.aicf_state import get_credits_user
    current_epoch = 100 // 100  # epoch 1
    credits = get_credits_user(state, current_epoch, miner_address)
    assert credits == 1_000_000  # credits_per_block
    
    # Verify inflow was tracked
    from execution.state.aicf_state import get_inflow
    inflow = get_inflow(state, current_epoch)
    assert inflow == 6_000_000_000  # 5 ANM + 1 ANM
    
    print("✓ AICF block processing test passed")


def test_aicf_epoch_boundary():
    """Test epoch finalization at boundary."""
    class MockState:
        def __init__(self):
            self.data = {}
        
        def get(self, key, default=None):
            return self.data.get(key, default)
        
        def put(self, key, value):
            self.data[key] = value
        
        def compute_state_root(self):
            return b"\x00" * 32
    
    class MockBlockEnv:
        def __init__(self, height):
            self.height = height
            self.timestamp = 1234567890
            self.coinbase = b"\x01" * 32
    
    state = MockState()
    miner_address = b"\x01" * 32
    
    params = {
        "aicf": {
            "epoch_length_blocks": 100,
            "credits_per_block": 1_000_000,
            "epoch_payout_bps": 5000,  # 50%
        }
    }
    
    # Initialize epoch length
    from execution.state.aicf_state import set_epoch_length, add_inflow
    set_epoch_length(state, 100)
    
    # Add some inflow to epoch 0
    add_inflow(state, 0, 10_000_000_000)  # 10 ANM
    
    # Process block at height 100 (epoch boundary)
    from execution.runtime.aicf_integration import process_block_for_aicf
    
    block_env = MockBlockEnv(100)
    process_block_for_aicf(
        state=state,
        block_env=block_env,
        miner_address=miner_address,
        block_reward_aicf_amount=1_000_000_000,
        fee_aicf_amount=500_000_000,
        params=params,
    )
    
    # Verify epoch 0 was finalized
    from execution.state.aicf_state import get_budget
    budget = get_budget(state, 0)
    assert budget == 5_000_000_000  # 50% of 10 ANM inflow
    
    # Verify epoch 1 got new inflow
    from execution.state.aicf_state import get_inflow
    inflow = get_inflow(state, 1)
    assert inflow == 1_500_000_000  # 1 ANM + 0.5 ANM
    
    print("✓ AICF epoch boundary test passed")


def test_aicf_claim_validation():
    """Test claim transaction validation."""
    class MockState:
        def __init__(self):
            self.data = {}
        
        def get(self, key, default=None):
            return self.data.get(key, default)
        
        def put(self, key, value):
            self.data[key] = value
        
        def compute_state_root(self):
            return b"\x00" * 32
    
    class MockBlockEnv:
        def __init__(self):
            self.height = 200
            self.timestamp = 1234567890
            self.coinbase = b"\x01" * 32
    
    class MockTxEnv:
        def __init__(self):
            self.sender = b"\x02" * 32
            self.gas_price = 1_000_000_000
    
    state = MockState()
    block_env = MockBlockEnv()
    tx_env = MockTxEnv()
    
    # Setup: Award some credits
    from execution.state.aicf_state import (
        set_epoch_length,
        add_credits,
        add_inflow,
        finalize_epoch,
    )
    
    set_epoch_length(state, 100)
    
    # Award 10M credits to user in epoch 0
    add_credits(state, 0, tx_env.sender, 10_000_000)
    
    # Add inflow and finalize epoch 0
    add_inflow(state, 0, 20_000_000_000)  # 20 ANM
    finalize_epoch(state, 0, 5000)  # 50% payout
    
    # Try to claim
    import cbor2
    claim_data = cbor2.dumps({
        "to_address": tx_env.sender,
        "amount": 5_000_000,  # Claim 5M credits
    })
    
    tx = {
        "kind": 4,  # AICF_CLAIM
        "data": claim_data,
    }
    
    params = {
        "aicf": {
            "epoch_length_blocks": 100,
            "max_claim_epochs": 100,
        }
    }
    
    # Execute claim
    from execution.runtime.aicf_claim import apply_aicf_claim
    
    result = apply_aicf_claim(tx, state, block_env, tx_env, params=params)
    
    # Should succeed
    from execution.types.status import TxStatus
    assert result.status == TxStatus.SUCCESS, f"Expected SUCCESS, got {result.status}"
    assert result.gas_used == 50000
    
    print("✓ AICF claim validation test passed")


def test_aicf_claim_overclaim_rejected():
    """Test that overclaim is rejected."""
    class MockState:
        def __init__(self):
            self.data = {}
        
        def get(self, key, default=None):
            return self.data.get(key, default)
        
        def put(self, key, value):
            self.data[key] = value
        
        def compute_state_root(self):
            return b"\x00" * 32
    
    class MockBlockEnv:
        def __init__(self):
            self.height = 200
            self.timestamp = 1234567890
            self.coinbase = b"\x01" * 32
    
    class MockTxEnv:
        def __init__(self):
            self.sender = b"\x02" * 32
            self.gas_price = 1_000_000_000
    
    state = MockState()
    block_env = MockBlockEnv()
    tx_env = MockTxEnv()
    
    # Setup: Award limited credits
    from execution.state.aicf_state import (
        set_epoch_length,
        add_credits,
        add_inflow,
        finalize_epoch,
    )
    
    set_epoch_length(state, 100)
    
    # Award only 1M credits
    add_credits(state, 0, tx_env.sender, 1_000_000)
    add_inflow(state, 0, 2_000_000_000)
    finalize_epoch(state, 0, 5000)
    
    # Try to claim more than available
    import cbor2
    claim_data = cbor2.dumps({
        "to_address": tx_env.sender,
        "amount": 5_000_000,  # Claim 5M credits (only 1M available)
    })
    
    tx = {
        "kind": 4,
        "data": claim_data,
    }
    
    params = {
        "aicf": {
            "epoch_length_blocks": 100,
            "max_claim_epochs": 100,
        }
    }
    
    # Execute claim
    from execution.runtime.aicf_claim import apply_aicf_claim
    
    result = apply_aicf_claim(tx, state, block_env, tx_env, params=params)
    
    # Should revert
    from execution.types.status import TxStatus
    assert result.status == TxStatus.REVERT, f"Expected REVERT, got {result.status}"
    
    # Check error message
    assert len(result.logs) > 0
    assert b"Insufficient claimable credits" in result.logs[0].data
    
    print("✓ AICF overclaim rejection test passed")


if __name__ == "__main__":
    test_aicf_block_processing_mock()
    test_aicf_epoch_boundary()
    test_aicf_claim_validation()
    test_aicf_claim_overclaim_rejected()
    print("\n✅ All AICF integration tests passed!")
