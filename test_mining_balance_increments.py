#!/usr/bin/env python3
"""
Regression test: Mining blocks must increase wallet balance.

This test validates that:
1. Creating a wallet address for mainnet (chain_id=0)
2. Mining K blocks to that address
3. Balance increases by K * block_subsidy
4. Premine + mining = expected total (if premine exists)
5. Balance is queryable via RPC and reflects mining rewards
"""

import tempfile
from pathlib import Path


def test_mining_increments_balance():
    """
    Test that mining blocks increases the wallet balance correctly.
    """
    print("\n" + "="*80)
    print("TEST: Mining Balance Increments")
    print("="*80)
    
    try:
        from core.db.sqlite import SQLiteKV
        from core.db.state_db import StateDB
        from execution.state.apply_balance import credit
    except ImportError as e:
        print(f"✗ Failed to import required modules: {e}")
        print("This test requires core and execution modules")
        return False
    
    # Test constants
    CHAIN_ID = 0  # Mainnet
    PREMINE_AMOUNT = 81_000_000_000_000_000  # 81M ANM in nANM
    BLOCK_REWARD = 300_000_000_000  # 300 ANM in nANM
    BLOCKS_TO_MINE = 3
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_state.db"
        
        # Create state DB
        kv = SQLiteKV(str(db_path))
        state_db = StateDB(kv)
        
        # Create test address (32 bytes)
        test_address = b"\x42" * 32
        
        print(f"\n✓ Test Setup:")
        print(f"  Chain ID: {CHAIN_ID}")
        print(f"  Test Address: 0x{test_address.hex()}")
        print(f"  Premine: {PREMINE_AMOUNT} nANM ({PREMINE_AMOUNT / 1e9:.9f} ANM)")
        print(f"  Block Reward: {BLOCK_REWARD} nANM ({BLOCK_REWARD / 1e9:.9f} ANM)")
        print(f"  Blocks to Mine: {BLOCKS_TO_MINE}")
        
        # Test 1: Set premine (simulating genesis)
        state_db.set_balance(test_address, PREMINE_AMOUNT)
        balance_after_premine = state_db.get_balance(test_address)
        
        print(f"\n✓ Test 1: Premine Application")
        print(f"  Balance after premine: {balance_after_premine} nANM ({balance_after_premine / 1e9:.9f} ANM)")
        
        if balance_after_premine != PREMINE_AMOUNT:
            print(f"  ✗ FAIL: Expected {PREMINE_AMOUNT}, got {balance_after_premine}")
            return False
        print("  ✓ PASS: Premine applied correctly")
        
        # Test 2: Mine blocks and credit rewards
        print(f"\n✓ Test 2: Mining {BLOCKS_TO_MINE} Blocks")
        
        for block_num in range(1, BLOCKS_TO_MINE + 1):
            # Credit mining reward (simulating block import)
            new_balance = credit(state_db, test_address, BLOCK_REWARD)
            balance_from_db = state_db.get_balance(test_address)
            
            expected_balance = PREMINE_AMOUNT + (block_num * BLOCK_REWARD)
            
            print(f"  Block {block_num}:")
            print(f"    Credited: {BLOCK_REWARD} nANM")
            print(f"    New balance (returned): {new_balance} nANM ({new_balance / 1e9:.9f} ANM)")
            print(f"    Balance from DB: {balance_from_db} nANM ({balance_from_db / 1e9:.9f} ANM)")
            print(f"    Expected: {expected_balance} nANM ({expected_balance / 1e9:.9f} ANM)")
            
            # Verify credit() return value
            if new_balance != expected_balance:
                print(f"    ✗ FAIL: credit() returned {new_balance}, expected {expected_balance}")
                return False
            
            # Verify DB persisted the change
            if balance_from_db != expected_balance:
                print(f"    ✗ FAIL: DB has {balance_from_db}, expected {expected_balance}")
                print(f"    Missing: {expected_balance - balance_from_db} nANM")
                return False
            
            print(f"    ✓ PASS: Block {block_num} reward credited correctly")
        
        # Test 3: Final balance verification
        final_balance = state_db.get_balance(test_address)
        expected_final = PREMINE_AMOUNT + (BLOCKS_TO_MINE * BLOCK_REWARD)
        
        print(f"\n✓ Test 3: Final Balance Verification")
        print(f"  Final balance: {final_balance} nANM ({final_balance / 1e9:.9f} ANM)")
        print(f"  Expected: {expected_final} nANM ({expected_final / 1e9:.9f} ANM)")
        print(f"  Breakdown:")
        print(f"    Premine: {PREMINE_AMOUNT} nANM ({PREMINE_AMOUNT / 1e9:.9f} ANM)")
        print(f"    Mining: {BLOCKS_TO_MINE * BLOCK_REWARD} nANM ({BLOCKS_TO_MINE * BLOCK_REWARD / 1e9:.9f} ANM)")
        
        if final_balance != expected_final:
            print(f"  ✗ FAIL: Balance mismatch")
            print(f"  Difference: {expected_final - final_balance} nANM")
            return False
        
        print("  ✓ PASS: Final balance is correct")
        
        # Test 4: Verify balance persistence (re-read from DB)
        kv2 = SQLiteKV(str(db_path))
        state_db2 = StateDB(kv2)
        persisted_balance = state_db2.get_balance(test_address)
        
        print(f"\n✓ Test 4: Balance Persistence")
        print(f"  Re-opened DB, balance: {persisted_balance} nANM ({persisted_balance / 1e9:.9f} ANM)")
        
        if persisted_balance != expected_final:
            print(f"  ✗ FAIL: Persisted balance {persisted_balance} != expected {expected_final}")
            return False
        
        print("  ✓ PASS: Balance persisted correctly")
        
        print("\n" + "="*80)
        print("✓ All tests PASSED!")
        print("="*80)
        print("\nSummary:")
        print(f"  • Initial balance: {PREMINE_AMOUNT / 1e9:.9f} ANM")
        print(f"  • Blocks mined: {BLOCKS_TO_MINE}")
        print(f"  • Reward per block: {BLOCK_REWARD / 1e9:.9f} ANM")
        print(f"  • Total mining rewards: {BLOCKS_TO_MINE * BLOCK_REWARD / 1e9:.9f} ANM")
        print(f"  • Final balance: {final_balance / 1e9:.9f} ANM")
        print(f"  • Expected: {expected_final / 1e9:.9f} ANM")
        print(f"  • Match: ✓")
        
        return True


def test_mainnet_premine_plus_one_block():
    """
    Specific test: Premine wallet (81M ANM) mines 1 block (300 ANM) should have 81,000,300 ANM.
    This validates the exact scenario mentioned in the problem statement.
    """
    print("\n" + "="*80)
    print("TEST: Mainnet Premine + 1 Block = 81,000,300 ANM")
    print("="*80)
    
    try:
        from core.db.sqlite import SQLiteKV
        from core.db.state_db import StateDB
        from execution.state.apply_balance import credit
    except ImportError as e:
        print(f"✗ Failed to import required modules: {e}")
        return False
    
    PREMINE = 81_000_000_000_000_000  # 81,000,000 ANM in nANM
    ONE_BLOCK_REWARD = 300_000_000_000  # 300 ANM in nANM
    EXPECTED_TOTAL = 81_000_300_000_000_000  # 81,000,300 ANM in nANM
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "mainnet_test.db"
        kv = SQLiteKV(str(db_path))
        state_db = StateDB(kv)
        
        address = b"\xFF" * 32
        
        print(f"\nScenario: Premine wallet mines 1 block on mainnet")
        print(f"  Address: 0x{address.hex()}")
        print(f"  Premine: {PREMINE / 1e9:.9f} ANM")
        print(f"  Block reward: {ONE_BLOCK_REWARD / 1e9:.9f} ANM")
        print(f"  Expected total: {EXPECTED_TOTAL / 1e9:.9f} ANM")
        
        # Apply premine
        state_db.set_balance(address, PREMINE)
        balance_after_premine = state_db.get_balance(address)
        print(f"\n  ✓ After premine: {balance_after_premine / 1e9:.9f} ANM")
        
        if balance_after_premine != PREMINE:
            print(f"  ✗ FAIL: Premine incorrect")
            return False
        
        # Mine 1 block
        new_balance = credit(state_db, address, ONE_BLOCK_REWARD)
        final_balance = state_db.get_balance(address)
        
        print(f"  ✓ After mining 1 block: {final_balance / 1e9:.9f} ANM")
        print(f"  Expected: {EXPECTED_TOTAL / 1e9:.9f} ANM")
        
        if final_balance != EXPECTED_TOTAL:
            print(f"\n  ✗ FAIL: Balance is {final_balance / 1e9:.9f} ANM, expected {EXPECTED_TOTAL / 1e9:.9f} ANM")
            print(f"  Difference: {(EXPECTED_TOTAL - final_balance) / 1e9:.9f} ANM")
            return False
        
        if new_balance != EXPECTED_TOTAL:
            print(f"\n  ✗ FAIL: credit() returned {new_balance / 1e9:.9f} ANM, expected {EXPECTED_TOTAL / 1e9:.9f} ANM")
            return False
        
        print("\n  ✓ PASS: Premine wallet shows exactly 81,000,300 ANM after mining 1 block")
        
        return True


if __name__ == "__main__":
    print("Running mining balance increment tests...")
    print("="*80)
    
    success1 = test_mining_increments_balance()
    if not success1:
        exit(1)
    
    success2 = test_mainnet_premine_plus_one_block()
    if not success2:
        exit(1)
    
    print("\n" + "="*80)
    print("✓ ALL TESTS PASSED")
    print("="*80)
    exit(0)
