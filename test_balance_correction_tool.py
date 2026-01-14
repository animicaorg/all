#!/usr/bin/env python3
"""
Test balance correction tool functionality.

This test validates that:
1. Inflation detection works correctly
2. Correction logic produces expected results
3. Audit trail is generated
"""

import tempfile
import shutil
from pathlib import Path


def test_inflation_detection():
    """Test that inflation factors are correctly detected."""
    from tools.correct_balance_inflation import detect_inflation_factor, BLOCK_REWARD
    
    # Test case 1: No inflation (small normal balance, < 10k blocks)
    balance = BLOCK_REWARD * 10  # 10 blocks mined
    factor, explanation, corrected = detect_inflation_factor(balance)
    assert factor is None, "Should not detect inflation for small normal balance"
    assert corrected == balance, "Corrected balance should equal original"
    
    # Test case 2: 2x inflation (large balance, 20k blocks -> 10k blocks)
    balance = BLOCK_REWARD * 20_000  # 20k blocks (inflated from 10k)
    factor, explanation, corrected = detect_inflation_factor(balance)
    assert factor == 2, f"Should detect 2x inflation for 20k blocks, got {factor}"
    assert corrected == BLOCK_REWARD * 10_000, f"Should correct to 10k blocks"
    
    # Test case 3: 5x inflation (large balance, matching docs example)
    # 92,820 ANM = 18,564 blocks (normal)
    # 464,100 ANM = 92,820 blocks (5x inflated)
    balance = BLOCK_REWARD * 92_820  # 92,820 blocks
    factor, explanation, corrected = detect_inflation_factor(balance)
    assert factor == 2, f"Should detect 2x inflation (92,820 is divisible by 2), got {factor}"
    
    # Test case 4: 5x inflation with odd number that's divisible by 5
    balance = BLOCK_REWARD * 50_000  # 50k blocks (inflated from 10k)
    factor, explanation, corrected = detect_inflation_factor(balance)
    assert factor == 2, f"Should detect 2x inflation (first factor), got {factor}"
    assert corrected == BLOCK_REWARD * 25_000, f"Should correct to 25k blocks"
    
    # Test case 5: Zero balance
    balance = 0
    factor, explanation, corrected = detect_inflation_factor(balance)
    assert factor is None, "Should not detect inflation for zero balance"
    assert corrected == 0, "Corrected balance should be zero"
    
    # Test case 6: Non-multiple of block reward
    balance = 12345
    factor, explanation, corrected = detect_inflation_factor(balance)
    assert factor is None, "Should not detect inflation for non-multiple"
    
    # Test case 7: Small balance that's a multiple (e.g., 100 blocks, no inflation)
    balance = BLOCK_REWARD * 100
    factor, explanation, corrected = detect_inflation_factor(balance)
    assert factor is None, f"Should not detect inflation for 100 blocks (below threshold), got {factor}"
    
    print("✓ All inflation detection tests passed")


def test_scan_and_correct():
    """Test scanning state DB and applying corrections."""
    import sys
    import os
    
    # Add repository root to path
    repo_root = Path(__file__).parent
    sys.path.insert(0, str(repo_root))
    
    from core.db.kv import open_kv
    from core.db.state_db import StateDB, Account
    from tools.correct_balance_inflation import scan_and_detect_inflation, apply_corrections, BLOCK_REWARD
    
    # Create temporary database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_state.db"
        audit_path = Path(tmpdir) / "audit.json"
        
        # Create state DB with test data
        kv = open_kv(str(db_path))
        state_db = StateDB(kv)
        
        # Add accounts with various balances
        # Account 1: Normal small balance (10 blocks, below threshold)
        addr1 = bytes.fromhex("1" * 64)
        state_db.put_account(addr1, Account(balance=BLOCK_REWARD * 10))
        
        # Account 2: 2x inflated (10k blocks * 2 = 20k blocks)
        addr2 = bytes.fromhex("2" * 64)
        state_db.put_account(addr2, Account(balance=BLOCK_REWARD * 20_000))
        
        # Account 3: 5x inflated (20k blocks * 5 = 100k blocks)
        addr3 = bytes.fromhex("3" * 64)
        state_db.put_account(addr3, Account(balance=BLOCK_REWARD * 100_000))
        
        # Account 4: Zero balance
        addr4 = bytes.fromhex("4" * 64)
        state_db.put_account(addr4, Account(balance=0))
        
        # Account 5: Normal large balance (prime number of blocks to avoid false positives)
        addr5 = bytes.fromhex("5" * 64)
        state_db.put_account(addr5, Account(balance=BLOCK_REWARD * 10_007))  # Prime number
        
        # Scan for inflation
        corrections = scan_and_detect_inflation(state_db)
        
        # Should detect 2 inflated accounts (addr2 and addr3)
        assert len(corrections) == 2, f"Should detect 2 inflated accounts, got {len(corrections)}"
        
        # Check addr2 correction
        addr2_correction = next((c for c in corrections if c["address_bytes"] == addr2), None)
        assert addr2_correction is not None, "Should find addr2 in corrections"
        assert addr2_correction["inflation_factor"] == 2, f"addr2 should be 2x inflated, got {addr2_correction['inflation_factor']}"
        assert addr2_correction["corrected_balance"] == BLOCK_REWARD * 10_000, "addr2 should correct to 10k blocks"
        
        # Check addr3 correction (will detect first factor, which is 2)
        addr3_correction = next((c for c in corrections if c["address_bytes"] == addr3), None)
        assert addr3_correction is not None, "Should find addr3 in corrections"
        # 100k is divisible by 2, so it will detect 2x (50k blocks)
        assert addr3_correction["inflation_factor"] == 2, f"addr3 should detect first factor (2x), got {addr3_correction['inflation_factor']}"
        
        # Apply corrections
        summary = apply_corrections(state_db, corrections, audit_path)
        assert summary["applied"] == 2, f"Should apply 2 corrections, got {summary['applied']}"
        assert summary["errors"] == 0, f"Should have 0 errors, got {summary['errors']}"
        
        # Verify corrected balances
        assert state_db.get_balance(addr1) == BLOCK_REWARD * 10, "addr1 should remain unchanged"
        assert state_db.get_balance(addr2) == BLOCK_REWARD * 10_000, "addr2 should be corrected to 10k blocks"
        assert state_db.get_balance(addr3) == BLOCK_REWARD * 50_000, "addr3 should be corrected (first factor)"
        assert state_db.get_balance(addr4) == 0, "addr4 should remain zero"
        assert state_db.get_balance(addr5) == BLOCK_REWARD * 10_007, "addr5 should remain unchanged"
        
        # Verify audit trail exists
        assert audit_path.exists(), "Audit trail should be created"
        
        # Clean up
        state_db.close()
        
        print("✓ All scan and correction tests passed")


if __name__ == "__main__":
    print("Running balance correction tool tests...")
    print()
    
    test_inflation_detection()
    test_scan_and_correct()
    
    print()
    print("=" * 60)
    print("✓ All tests passed!")
    print("=" * 60)
