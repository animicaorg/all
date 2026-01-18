"""
Test to verify that the credit() function works correctly with StateDB.

This is a simple unit test that verifies the credit() function from 
execution.state.apply_balance properly updates balances in StateDB.

Note: This test does NOT reproduce the full block mining scenario.
It only tests that the basic credit mechanism works.
"""

import tempfile
import os
import sys
from pathlib import Path


def test_credit_function_with_state_db():
    """Test that credit() function correctly updates balances in StateDB."""
    
    # Create temporary directory for test database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_state.db"
        
        # Import required modules
        from core.db.sqlite import SQLiteKV
        from core.db.state_db import StateDB
        from execution.state.apply_balance import credit
        
        # Create state DB
        kv = SQLiteKV(str(db_path))
        state_db = StateDB(kv)
        
        # Test address (arbitrary 32 bytes)
        test_address_bytes = b"\x01" * 32
        
        # Test amounts (simulating premine + mining reward)
        PREMINE_AMOUNT = 81_000_000_000_000_000  # 81M ANM in nANM
        MINING_REWARD = 300_000_000_000  # 300 ANM in nANM
        EXPECTED_TOTAL = PREMINE_AMOUNT + MINING_REWARD
        
        # Set initial balance (simulating genesis premine)
        state_db.set_balance(test_address_bytes, PREMINE_AMOUNT)
        
        # Verify initial balance
        balance_after_genesis = state_db.get_balance(test_address_bytes)
        assert balance_after_genesis == PREMINE_AMOUNT, (
            f"Initial balance incorrect: expected {PREMINE_AMOUNT}, "
            f"got {balance_after_genesis}"
        )
        print(f"✓ Initial balance: {balance_after_genesis} nANM ({balance_after_genesis / 1e9:.9f} ANM)")
        
        # Credit mining reward (simulating what _apply_block_reward does)
        new_balance = credit(state_db, test_address_bytes, MINING_REWARD)
        print(f"✓ After credit: {new_balance} nANM ({new_balance / 1e9:.9f} ANM)")
        
        # Verify the balance was actually updated in state_db
        balance_after_mining = state_db.get_balance(test_address_bytes)
        print(f"✓ Verified balance: {balance_after_mining} nANM ({balance_after_mining / 1e9:.9f} ANM)")
        
        # Check that credit() returned correct value
        assert new_balance == EXPECTED_TOTAL, (
            f"credit() return value incorrect:\n"
            f"  Expected: {EXPECTED_TOTAL} nANM ({EXPECTED_TOTAL / 1e9:.9f} ANM)\n"
            f"  Got:      {new_balance} nANM ({new_balance / 1e9:.9f} ANM)"
        )
        
        # Check that state_db actually persisted the change
        assert balance_after_mining == EXPECTED_TOTAL, (
            f"Balance in state_db incorrect after credit:\n"
            f"  Expected: {EXPECTED_TOTAL} nANM ({EXPECTED_TOTAL / 1e9:.9f} ANM)\n"
            f"  Got:      {balance_after_mining} nANM ({balance_after_mining / 1e9:.9f} ANM)\n"
            f"  Missing:  {EXPECTED_TOTAL - balance_after_mining} nANM "
            f"({(EXPECTED_TOTAL - balance_after_mining) / 1e9:.9f} ANM)"
        )
        
        print(f"✓ Test passed: credit() function works correctly with StateDB")


if __name__ == "__main__":
    test_credit_function_with_state_db()
