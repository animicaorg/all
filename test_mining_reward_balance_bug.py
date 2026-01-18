#!/usr/bin/env python3
"""
Test to reproduce the mining reward balance bug.

Expected behavior:
1. Start with premine balance = 81,000,000 ANM
2. Mine 1 block to premine address
3. Balance should be 81,000,300 ANM

Actual behavior (if bug exists):
1. Balance stays 81,000,000 ANM after mining

This test mines directly via RPC to isolate the issue from CLI/wallet code.
"""

import os
import sys
import time
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root))

def test_mining_reward_balance():
    """Test that mining rewards are actually credited to wallet balance."""
    print("=" * 80)
    print("Testing Mining Reward Balance Bug")
    print("=" * 80)
    
    # Import here to ensure proper path
    from rpc.deps import get_ctx
    from rpc.methods.state import get_balance
    from core.chain.block_import import BlockImporter
    from core.db.block_db import BlockDB
    from core.db.state_db import StateDB
    from core.types.params import ChainParams
    from consensus.rewards import compute_block_reward
    
    # Premine address (mainnet)
    premine_addr = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    
    # Expected values
    premine_initial = 81_000_000_000_000_000  # 81M ANM in base units
    block_reward = 300_000_000_000  # 300 ANM in base units
    expected_after_1_block = premine_initial + block_reward
    
    print(f"\nPremine address: {premine_addr}")
    print(f"Expected initial balance: {premine_initial / 1e9:.9f} ANM")
    print(f"Expected reward per block: {block_reward / 1e9:.9f} ANM")
    print(f"Expected balance after 1 block: {expected_after_1_block / 1e9:.9f} ANM")
    
    # TODO: Set up test environment and mine 1 block
    # For now, this is just a template showing what we need to test
    
    print("\n" + "=" * 80)
    print("Test skeleton created - needs implementation")
    print("=" * 80)
    
    return True

if __name__ == "__main__":
    success = test_mining_reward_balance()
    sys.exit(0 if success else 1)
