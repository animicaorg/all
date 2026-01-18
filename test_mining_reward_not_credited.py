"""
Test to reproduce mining reward not being credited issue.

This test reproduces the scenario from the problem statement:
1. Genesis has an address with 81M ANM premine
2. Mine 1 block with 300 ANM reward to that address
3. Check balance - should be 81M + 300 ANM

Expected: Balance = 81,000,300 ANM
Actual (BUG): Balance = 81,000,000 ANM (reward not credited)
"""

import tempfile
import os
import sys
from pathlib import Path


def test_mining_reward_credited_to_premine_address():
    """Test that mining rewards are properly credited to addresses with premine balance."""
    
    # Create temporary directories for test
    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir = Path(tmpdir) / "chain"
        chain_dir.mkdir()
        
        # Import required modules
        from core.db.state_db import StateDB
        from execution.state.apply_balance import credit
        
        # Helper to get balance
        def get_balance(state_db, address_bytes):
            return state_db.get_balance(address_bytes)
        
        # Test address (from genesis.json)
        test_address_bech32 = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
        
        # Decode address to bytes (32-byte digest)
        from pq.py.address import decode_address
        addr_record = decode_address(test_address_bech32)
        test_address_bytes = bytes(addr_record.digest)[:32].ljust(32, b"\x00")
        
        # Expected values
        PREMINE_AMOUNT = 81_000_000_000_000_000  # 81M ANM in nANM
        MINING_REWARD = 300_000_000_000  # 300 ANM in nANM
        EXPECTED_TOTAL = PREMINE_AMOUNT + MINING_REWARD
        
        # Initialize state DB
        state_db_path = chain_dir / "state.db"
        state_db = StateDB(str(state_db_path))
        
        # Load genesis (which should set premine balance)
        genesis_path = Path(__file__).parent / "core" / "genesis" / "genesis.json"
        assert genesis_path.exists(), f"Genesis file not found: {genesis_path}"
        
        # Manually initialize genesis state for this test
        # (simulating what load_and_init_genesis does)
        state_db.set_balance(test_address_bytes, PREMINE_AMOUNT)
        
        # Verify premine balance
        balance_after_genesis = get_balance(state_db, test_address_bytes)
        assert balance_after_genesis == PREMINE_AMOUNT, (
            f"Premine balance incorrect: expected {PREMINE_AMOUNT}, "
            f"got {balance_after_genesis}"
        )
        print(f"✓ Genesis balance: {balance_after_genesis} nANM ({balance_after_genesis / 1e9:.9f} ANM)")
        
        # Simulate mining reward credit (what _apply_block_reward should do)
        new_balance = credit(state_db, test_address_bytes, MINING_REWARD)
        print(f"✓ After credit: {new_balance} nANM ({new_balance / 1e9:.9f} ANM)")
        
        # Verify the balance increased
        balance_after_mining = get_balance(state_db, test_address_bytes)
        print(f"✓ Final balance: {balance_after_mining} nANM ({balance_after_mining / 1e9:.9f} ANM)")
        
        assert balance_after_mining == EXPECTED_TOTAL, (
            f"Balance after mining incorrect:\n"
            f"  Expected: {EXPECTED_TOTAL} nANM ({EXPECTED_TOTAL / 1e9:.9f} ANM)\n"
            f"  Got:      {balance_after_mining} nANM ({balance_after_mining / 1e9:.9f} ANM)\n"
            f"  Missing:  {EXPECTED_TOTAL - balance_after_mining} nANM "
            f"({(EXPECTED_TOTAL - balance_after_mining) / 1e9:.9f} ANM)"
        )
        
        print(f"✓ Test passed: Mining reward was properly credited")


if __name__ == "__main__":
    test_mining_reward_credited_to_premine_address()
