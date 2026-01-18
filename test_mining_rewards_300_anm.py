#!/usr/bin/env python3
"""
Test script to verify that all networks have 300 ANM block rewards and 5-minute block time.
This validates the changes made to spec/params.yaml for devnet and testnet.
"""

import sys
import traceback

sys.path.insert(0, '.')

from consensus.rewards import compute_block_reward
from rpc.deps import _params_from_spec
import yaml
from pathlib import Path

# Expected values
EXPECTED_BLOCK_REWARD_NANM = 300_000_000_000  # 300 ANM in nANM
EXPECTED_BLOCK_TIME_MS = 300_000  # 5 minutes in milliseconds
EXPECTED_MINER_SPLIT_PCT = 100
EXPECTED_AICF_SPLIT_PCT = 0
EXPECTED_TREASURY_SPLIT_PCT = 0
EXPECTED_REWARD_COUNT = 1  # Number of reward entries (100% to miner)
NANM_TO_ANM_CONVERSION = 1e9  # Conversion factor: 1 ANM = 10^9 nANM

def test_network(network_name, chain_id, network_key):
    """Test a specific network's rewards and block time."""
    print(f"\n{'='*70}")
    print(f"Testing {network_name} (chain_id={chain_id})")
    print('='*70)
    
    # Load params using RPC deps method (simulates how node loads params)
    params = _params_from_spec(chain_id)
    
    # Test block time
    issuance = params.get('monetary', {}).get('issuance', {})
    block_time_ms = issuance.get('target_block_interval_ms')
    
    if block_time_ms is None:
        raise ValueError(f"{network_name}: Missing 'target_block_interval_ms' in params")
    
    block_time_min = block_time_ms / 1000 / 60
    
    print(f"Block time: {block_time_ms} ms = {block_time_min:.1f} minutes")
    assert block_time_ms == EXPECTED_BLOCK_TIME_MS, \
        f"Expected {EXPECTED_BLOCK_TIME_MS} ms (5 min), got {block_time_ms} ms"
    print("  ✓ Block time is correct (5 minutes)")
    
    # Test block reward at various heights
    for height in [1, 2, 10, 100]:
        rewards = compute_block_reward(chain_id=chain_id, height=height, params=params)
        
        if not rewards:
            print(f"  ✗ Height {height}: No rewards returned!")
            return False
        
        # Should have exactly 1 reward (100% to miner)
        assert len(rewards) == EXPECTED_REWARD_COUNT, \
            f"Expected {EXPECTED_REWARD_COUNT} reward, got {len(rewards)}"
        
        addr, amount = rewards[0]
        amount_anm = amount / NANM_TO_ANM_CONVERSION
        
        print(f"  Height {height}: {amount_anm:.1f} ANM ({amount} nANM)")
        assert amount == EXPECTED_BLOCK_REWARD_NANM, \
            f"Expected {EXPECTED_BLOCK_REWARD_NANM / 1e9:.1f} ANM, got {amount_anm} ANM"
    
    print(f"  ✓ All rewards are {EXPECTED_BLOCK_REWARD_NANM / NANM_TO_ANM_CONVERSION:.0f} ANM")
    
    # Verify subsidy split (should be 100% miner)
    split = issuance.get('subsidy_split_pct', {})
    assert split.get('miner') == EXPECTED_MINER_SPLIT_PCT, \
        f"Expected {EXPECTED_MINER_SPLIT_PCT}% miner, got {split.get('miner')}%"
    assert split.get('aicf') == EXPECTED_AICF_SPLIT_PCT, \
        f"Expected {EXPECTED_AICF_SPLIT_PCT}% AICF, got {split.get('aicf')}%"
    assert split.get('treasury') == EXPECTED_TREASURY_SPLIT_PCT, \
        f"Expected {EXPECTED_TREASURY_SPLIT_PCT}% treasury, got {split.get('treasury')}%"
    print(f"  ✓ Subsidy split is {EXPECTED_MINER_SPLIT_PCT}% to miner")
    
    return True

def main():
    """Test all networks."""
    print("\n" + "="*70)
    print("Block Rewards and Block Time Validation")
    print("="*70)
    print("\nVerifying that all networks have:")
    print("  - Block rewards: 300 ANM")
    print("  - Block time: 5 minutes")
    
    networks = [
        ("Mainnet", 1, "animica:1"),
        ("Testnet", 2, "animica:2"),
        ("Devnet", 1337, "animica:1337"),
    ]
    
    passed = 0
    failed = 0
    
    for network_name, chain_id, network_key in networks:
        try:
            if test_network(network_name, chain_id, network_key):
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            failed += 1
            traceback.print_exc()
    
    print("\n" + "="*70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("="*70)
    
    if failed == 0:
        print("\n✓ SUCCESS: All networks have 300 ANM rewards and 5-minute block time!")
        return 0
    else:
        print(f"\n✗ FAILURE: {failed} network(s) failed validation")
        return 1

if __name__ == "__main__":
    sys.exit(main())
