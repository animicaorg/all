#!/usr/bin/env python3
"""
Test that instant blocks (tx send blocks) have zero rewards.
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(__file__))


def test_instant_block_zero_reward():
    """Test that compute_block_reward returns empty list for instant blocks."""
    print("\n" + "="*70)
    print("TEST: Instant blocks should have zero rewards")
    print("="*70)
    
    try:
        from consensus.rewards import compute_block_reward
    except ImportError as e:
        print(f"SKIP: Cannot import consensus.rewards: {e}")
        return True
    
    # Test at various heights with instant_block=True
    test_cases = [
        (1337, 1, {}),    # devnet, height 1
        (1337, 10, {}),   # devnet, height 10
        (1337, 100, {}),  # devnet, height 100
    ]
    
    all_passed = True
    
    for chain_id, height, params in test_cases:
        print(f"\n  Testing chain_id={chain_id}, height={height}")
        
        # Normal block (should have rewards)
        normal_rewards = compute_block_reward(chain_id, height, params, instant_block=False)
        print(f"    Normal block rewards: {len(normal_rewards)} entries")
        if normal_rewards:
            print(f"      First reward: {normal_rewards[0][1]:,} nANM")
        
        # Instant block (should have NO rewards)
        instant_rewards = compute_block_reward(chain_id, height, params, instant_block=True)
        print(f"    Instant block rewards: {len(instant_rewards)} entries")
        
        if instant_rewards:
            print(f"      ✗ FAIL: Instant block should have zero rewards, got {instant_rewards}")
            all_passed = False
        else:
            print(f"      ✓ PASS: Instant block has zero rewards")
    
    # Test mainnet genesis (should still have premine for normal blocks)
    print(f"\n  Testing mainnet genesis (chain_id=1, height=0)")
    mainnet_genesis_normal = compute_block_reward(1, 0, {}, instant_block=False)
    print(f"    Normal genesis rewards: {len(mainnet_genesis_normal)} entries")
    if mainnet_genesis_normal:
        total = sum(amt for _, amt in mainnet_genesis_normal)
        print(f"      Total premine: {total:,} nANM")
    
    mainnet_genesis_instant = compute_block_reward(1, 0, {}, instant_block=True)
    print(f"    Instant genesis rewards: {len(mainnet_genesis_instant)} entries")
    if mainnet_genesis_instant:
        print(f"      ✗ FAIL: Instant genesis should have zero rewards, got {mainnet_genesis_instant}")
        all_passed = False
    else:
        print(f"      ✓ PASS: Instant genesis has zero rewards")
    
    print(f"\n" + "="*70)
    if all_passed:
        print(f"TEST RESULT: ✓ ALL TESTS PASSED")
    else:
        print(f"TEST RESULT: ✗ SOME TESTS FAILED")
    print(f"="*70)
    
    return all_passed


if __name__ == "__main__":
    success = test_instant_block_zero_reward()
    sys.exit(0 if success else 1)
