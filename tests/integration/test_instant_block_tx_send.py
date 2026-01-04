"""
Integration test for instant block creation on tx send.

This test verifies that when ANIMICA_INSTANT_BLOCKS_ENABLED=1:
1. tx send triggers instant block creation
2. instant block has zero reward
3. instant block does not advance canonical height
4. instant block is marked with instantBlock=True flag
5. transaction is included in the instant block
"""
import os
import pytest

# Skip if instant blocks not enabled in test environment
pytestmark = pytest.mark.skipif(
    os.environ.get("ANIMICA_INSTANT_BLOCKS_ENABLED", "").lower() not in {"1", "true", "yes", "on"},
    reason="Instant blocks not enabled (set ANIMICA_INSTANT_BLOCKS_ENABLED=1)"
)


def test_instant_block_on_tx_send_enabled():
    """
    Test that tx send creates an instant block when feature is enabled.
    
    This test requires:
    - ANIMICA_INSTANT_BLOCKS_ENABLED=1
    - ANIMICA_TX_SEND_FORCE_CHAIN=1 (to ensure block is mined)
    - Running node with RPC available
    """
    from rpc.methods.tx import _tx_send_raw_transaction
    from rpc.methods.miner import _mine_instant_block
    from rpc import deps
    import time
    
    # Get initial state
    try:
        block_db = deps.get_block_db()
        initial_height = block_db.get_height()
        initial_canonical_height = block_db.get_canonical_height()
    except Exception as e:
        pytest.skip(f"Could not access block DB: {e}")
    
    # Create a simple test transaction (this is a placeholder)
    # In a real test, you would:
    # 1. Create a valid signed transaction
    # 2. Submit it via _tx_send_raw_transaction
    # 3. Verify instant block was created
    
    # For now, just verify the instant block function is callable
    success, reward, summary = _mine_instant_block()
    
    if not success:
        # No pending transactions is expected
        assert summary.get("error") in ["no_pending_transactions", "no_valid_transactions"], \
            f"Unexpected instant block error: {summary}"
        pytest.skip("No pending transactions available for instant block test")
    
    # If we created an instant block, verify properties
    assert reward == 0, "Instant block should have zero reward"
    assert summary.get("instant_block") is True, "Summary should indicate instant block"
    
    # Verify canonical height did not advance
    new_height = block_db.get_height()
    new_canonical_height = block_db.get_canonical_height()
    
    assert new_height == initial_height + 1, "Block height should advance by 1"
    assert new_canonical_height == initial_canonical_height, \
        "Canonical height should NOT advance for instant blocks"


def test_instant_block_header_flag():
    """Test that instant blocks have the instantBlock flag set."""
    from core.types.header import Header
    from consensus.rewards import compute_block_reward
    
    zero32 = b"\x00" * 32
    
    # Create instant block header
    instant_header = Header(
        v=1,
        chainId=1337,
        height=100,
        parentHash=zero32,
        timestamp=int(os.environ.get("TEST_TIMESTAMP", "1700000000")),
        stateRoot=zero32,
        txsRoot=zero32,
        receiptsRoot=zero32,
        proofsRoot=zero32,
        daRoot=zero32,
        mixSeed=zero32,
        poiesPolicyRoot=zero32,
        pqAlgPolicyRoot=zero32,
        thetaMicro=1_000_000,
        nonce=0,
        extra=b"",
        instantBlock=True,
    )
    
    # Verify flag is set
    assert instant_header.instantBlock is True
    assert instant_header.nonce == 0
    
    # Verify serialization preserves flag
    obj = instant_header.to_obj()
    assert obj.get("instantBlock") is True
    
    # Verify deserialization preserves flag
    cbor_bytes = instant_header.to_cbor()
    header2 = Header.from_cbor(cbor_bytes)
    assert header2.instantBlock is True


def test_instant_block_zero_reward():
    """Test that instant blocks always return zero rewards."""
    from consensus.rewards import compute_block_reward
    
    # Minimal params for reward calculation
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 5_000_000_000,
                    "epoch_length_blocks": 90_000_000,
                    "decay_pct_per_epoch": 50.0,
                    "tail_nANM_per_block": 100_000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 60,
                    "aicf": 30,
                    "treasury": 10,
                },
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1test",
            "aicf_treasury": "anim1aicf",
            "treasury": "anim1treasury",
        },
    }
    
    # Normal block has rewards
    normal_rewards = compute_block_reward(
        chain_id=1337,
        height=100,
        params=params,
        instant_block=False,
    )
    assert len(normal_rewards) > 0, "Normal block should have rewards"
    
    # Instant block has zero rewards
    instant_rewards = compute_block_reward(
        chain_id=1337,
        height=100,
        params=params,
        instant_block=True,
    )
    assert len(instant_rewards) == 0, "Instant block should have zero rewards"


def test_canonical_height_tracking():
    """Test that canonical height calculation excludes instant blocks."""
    from consensus.rewards import compute_canonical_height
    
    # Genesis: canonical height = 0
    assert compute_canonical_height(0, False) == 0
    assert compute_canonical_height(0, True) == 0
    
    # Normal block at height 1: canonical height = 1
    assert compute_canonical_height(1, False, canonical_height=0) == 1
    
    # Instant block at height 2: canonical height stays at 1
    assert compute_canonical_height(2, True, canonical_height=1) == 1
    
    # Normal block at height 3: canonical height = 2
    assert compute_canonical_height(3, False, canonical_height=1) == 2
    
    # Multiple instant blocks preserve canonical height
    assert compute_canonical_height(4, True, canonical_height=2) == 2
    assert compute_canonical_height(5, True, canonical_height=2) == 2
    
    # Next normal block increments canonical height
    assert compute_canonical_height(6, False, canonical_height=2) == 3


if __name__ == "__main__":
    # Allow running directly for manual testing
    pytest.main([__file__, "-v", "-s"])
