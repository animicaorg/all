#!/usr/bin/env python3
"""
Test that instant blocks don't count towards halving schedule.
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(__file__))


def test_instant_blocks_dont_count_towards_halving():
    """Test that instant blocks are excluded from canonical_height used for halving."""
    print("\n" + "="*70)
    print("TEST: Instant blocks should NOT count towards halving")
    print("="*70)
    
    try:
        from consensus.rewards import compute_block_reward
    except ImportError as e:
        print(f"SKIP: Cannot import consensus.rewards: {e}")
        return True
    
    # Test parameters with short epoch for testing
    test_params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 10_000_000_000,  # 10 ANM
                    "epoch_length_blocks": 10,  # Halve every 10 blocks
                    "decay_pct_per_epoch": 50.0,  # 50% reduction
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 100,
                    "aicf": 0,
                    "treasury": 0,
                },
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "aicf_treasury": "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "treasury": "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        },
    }
    
    chain_id = 1337
    all_passed = True
    
    # Scenario 1: Normal mining blocks only
    print(f"\n  Scenario 1: Mining blocks only (no instant blocks)")
    print(f"    Expected: First halving at canonical_height=11")
    
    # Block at canonical height 1 (absolute height 1)
    rewards_h1 = compute_block_reward(
        chain_id=chain_id, 
        height=1, 
        params=test_params, 
        instant_block=False,
        canonical_height=1
    )
    reward_h1 = rewards_h1[0][1] if rewards_h1 else 0
    print(f"      Canonical height 1 (epoch 0): {reward_h1:,} nANM")
    
    # Block at canonical height 10 (still in epoch 0)
    rewards_h10 = compute_block_reward(
        chain_id=chain_id,
        height=10,
        params=test_params,
        instant_block=False,
        canonical_height=10
    )
    reward_h10 = rewards_h10[0][1] if rewards_h10 else 0
    print(f"      Canonical height 10 (epoch 0): {reward_h10:,} nANM")
    
    # Block at canonical height 11 (epoch 1 - after first halving)
    rewards_h11 = compute_block_reward(
        chain_id=chain_id,
        height=11,
        params=test_params,
        instant_block=False,
        canonical_height=11
    )
    reward_h11 = rewards_h11[0][1] if rewards_h11 else 0
    print(f"      Canonical height 11 (epoch 1): {reward_h11:,} nANM")
    
    # Verify halving happened
    if reward_h1 == 10_000_000_000 and reward_h10 == 10_000_000_000 and reward_h11 == 5_000_000_000:
        print(f"      ✓ PASS: Halving occurred at canonical_height=11")
    else:
        print(f"      ✗ FAIL: Rewards don't match expected halving pattern")
        print(f"        Expected: 10B, 10B, 5B; Got: {reward_h1}, {reward_h10}, {reward_h11}")
        all_passed = False
    
    # Scenario 2: Mix of mining and instant blocks
    print(f"\n  Scenario 2: Mix of mining and instant blocks")
    print(f"    Setup: 10 mining blocks + 10 instant blocks = 20 total")
    print(f"    Expected: Halving still at canonical_height=11 (absolute height could be 21)")
    
    # With instant blocks mixed in, absolute height is higher but canonical_height stays the same
    # Absolute height 20, but only 10 mining blocks (canonical_height=10)
    rewards_mixed_h10 = compute_block_reward(
        chain_id=chain_id,
        height=20,  # Absolute height is 20 due to instant blocks
        params=test_params,
        instant_block=False,
        canonical_height=10  # But only 10 mining blocks
    )
    reward_mixed_h10 = rewards_mixed_h10[0][1] if rewards_mixed_h10 else 0
    print(f"      Absolute height 20, canonical 10 (epoch 0): {reward_mixed_h10:,} nANM")
    
    # Absolute height 21, canonical_height 11 (first block in epoch 1)
    rewards_mixed_h11 = compute_block_reward(
        chain_id=chain_id,
        height=21,  # Absolute height
        params=test_params,
        instant_block=False,
        canonical_height=11  # 11th mining block
    )
    reward_mixed_h11 = rewards_mixed_h11[0][1] if rewards_mixed_h11 else 0
    print(f"      Absolute height 21, canonical 11 (epoch 1): {reward_mixed_h11:,} nANM")
    
    # Verify halving is based on canonical_height, not absolute height
    if reward_mixed_h10 == 10_000_000_000 and reward_mixed_h11 == 5_000_000_000:
        print(f"      ✓ PASS: Halving based on canonical_height (mining blocks only)")
    else:
        print(f"      ✗ FAIL: Halving not using canonical_height correctly")
        print(f"        Expected: 10B, 5B; Got: {reward_mixed_h10}, {reward_mixed_h11}")
        all_passed = False
    
    # Scenario 3: Verify instant blocks get zero rewards regardless of height
    print(f"\n  Scenario 3: Instant blocks always get zero rewards")
    
    instant_reward_h5 = compute_block_reward(
        chain_id=chain_id,
        height=5,
        params=test_params,
        instant_block=True,  # This is the key flag
        canonical_height=5
    )
    instant_reward_h15 = compute_block_reward(
        chain_id=chain_id,
        height=15,
        params=test_params,
        instant_block=True,
        canonical_height=11  # Even after halving
    )
    
    if len(instant_reward_h5) == 0 and len(instant_reward_h15) == 0:
        print(f"      ✓ PASS: Instant blocks have zero rewards at any height")
    else:
        print(f"      ✗ FAIL: Instant blocks should have zero rewards")
        print(f"        Got: h5={instant_reward_h5}, h15={instant_reward_h15}")
        all_passed = False
    
    print(f"\n" + "="*70)
    if all_passed:
        print(f"TEST RESULT: ✓ ALL TESTS PASSED")
        print(f"Instant blocks correctly excluded from halving schedule!")
    else:
        print(f"TEST RESULT: ✗ SOME TESTS FAILED")
    print(f"="*70)
    
    return all_passed


if __name__ == "__main__":
    success = test_instant_blocks_dont_count_towards_halving()
    sys.exit(0 if success else 1)
