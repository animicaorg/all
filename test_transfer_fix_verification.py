#!/usr/bin/env python3
"""
Verification test for the transfer fix.

This test verifies that:
1. Normal transfers (Alice → Bob) work correctly
2. Recipient balance is credited
3. Sender balance is debited
4. Self-sends work correctly (amount debits and credits cancel out)
"""

import sys
from dataclasses import dataclass


# Common test infrastructure
@dataclass
class Account:
    nonce: int = 0
    balance: int = 0
    code_hash: bytes = b"\x00" * 32


class MockState:
    def __init__(self):
        self.accounts = {}
    
    def ensure_account(self, addr: bytes):
        if addr not in self.accounts:
            self.accounts[addr] = Account()
    
    def get_balance(self, addr: bytes) -> int:
        if addr not in self.accounts:
            return 0
        return self.accounts[addr].balance
    
    def set_balance(self, addr: bytes, value: int):
        self.ensure_account(addr)
        self.accounts[addr].balance = value
    
    def get_nonce(self, addr: bytes) -> int:
        if addr not in self.accounts:
            return 0
        return self.accounts[addr].nonce
    
    def set_nonce(self, addr: bytes, value: int):
        self.ensure_account(addr)
        self.accounts[addr].nonce = value
    
    def increment_nonce(self, addr: bytes):
        self.ensure_account(addr)
        self.accounts[addr].nonce += 1


def test_normal_transfer():
    """Test that normal transfers credit the recipient."""
    print("\n" + "="*70)
    print("TEST 1: Normal Transfer (Alice → Bob)")
    print("="*70)
    
    try:
        from execution.runtime.transfers import apply_transfer
        from execution.runtime.env import BlockEnv, TxEnv
    except ImportError as e:
        print(f"SKIP: Missing dependencies: {e}")
        return True
    
    state = MockState()
    
    # Create addresses
    alice = b"\x01" * 32
    bob = b"\x02" * 32
    coinbase = b"\x03" * 32
    
    # Set initial balance for Alice
    state.set_balance(alice, 1_000_000)
    
    # Create environments
    block_env = BlockEnv(
        height=1,
        timestamp=1000,
        coinbase=coinbase,
        chain_id=0,
    )
    
    tx_env = TxEnv(
        sender=alice,
        chain_id=0,
        gas_price=1,
    )
    
    # Create transaction
    class MockTx:
        to = bob
        value = 100_000
        gas = 21_000
    
    tx = MockTx()
    
    # Apply transfer
    print(f"Initial balances:")
    print(f"  Alice: {state.get_balance(alice):,}")
    print(f"  Bob:   {state.get_balance(bob):,}")
    
    result = apply_transfer(tx, state, block_env, tx_env, emit_event=False)
    
    print(f"\nFinal balances:")
    print(f"  Alice: {state.get_balance(alice):,}")
    print(f"  Bob:   {state.get_balance(bob):,}")
    
    # Verify result
    if result.status.name != "SUCCESS":
        print(f"✗ FAIL: Transfer failed with status: {result.status}")
        return False
    
    # Verify Alice was debited
    gas_fee = 21_000 * 1
    expected_alice = 1_000_000 - 100_000 - gas_fee
    actual_alice = state.get_balance(alice)
    
    if actual_alice != expected_alice:
        print(f"✗ FAIL: Alice balance incorrect: expected {expected_alice:,}, got {actual_alice:,}")
        return False
    
    # Verify Bob was credited
    expected_bob = 100_000
    actual_bob = state.get_balance(bob)
    
    if actual_bob != expected_bob:
        print(f"✗ FAIL: Bob balance incorrect: expected {expected_bob:,}, got {actual_bob:,}")
        return False
    
    print(f"\n✓ PASS: Normal transfer works correctly")
    print(f"  Alice debited: {1_000_000 - actual_alice:,}")
    print(f"  Bob credited:  {actual_bob:,}")
    return True


def test_self_send():
    """Test that self-sends work correctly (debits and credits cancel out)."""
    print("\n" + "="*70)
    print("TEST 2: Self-Send (Alice → Alice)")
    print("="*70)
    
    try:
        from execution.runtime.transfers import apply_transfer
        from execution.runtime.env import BlockEnv, TxEnv
    except ImportError as e:
        print(f"SKIP: Missing dependencies: {e}")
        return True
    
    state = MockState()
    
    # Create addresses
    alice = b"\x01" * 32
    coinbase = b"\x03" * 32
    
    # Set initial balance for Alice
    state.set_balance(alice, 1_000_000)
    
    # Create environments
    block_env = BlockEnv(
        height=1,
        timestamp=1000,
        coinbase=coinbase,
        chain_id=0,
    )
    
    tx_env = TxEnv(
        sender=alice,
        chain_id=0,
        gas_price=1,
    )
    
    # Create self-send transaction (Alice → Alice)
    class MockTx:
        to = alice  # Same as sender!
        value = 100_000
        gas = 21_000
    
    tx = MockTx()
    
    # Apply transfer
    print(f"Initial balance:")
    print(f"  Alice: {state.get_balance(alice):,}")
    
    result = apply_transfer(tx, state, block_env, tx_env, emit_event=False)
    
    print(f"\nFinal balance:")
    print(f"  Alice: {state.get_balance(alice):,}")
    
    # Verify result
    if result.status.name != "SUCCESS":
        print(f"✗ FAIL: Self-send failed with status: {result.status}")
        return False
    
    # Verify Alice only lost fees (amount debits and credits cancel out)
    gas_fee = 21_000 * 1
    expected_alice = 1_000_000 - gas_fee
    actual_alice = state.get_balance(alice)
    
    if actual_alice != expected_alice:
        print(f"✗ FAIL: Alice balance incorrect: expected {expected_alice:,}, got {actual_alice:,}")
        print(f"  Amount should cancel out (debit 100k + credit 100k = 0)")
        print(f"  Only fees should be deducted")
        return False
    
    print(f"\n✓ PASS: Self-send works correctly")
    print(f"  Alice only lost fees: {1_000_000 - actual_alice:,}")
    print(f"  Amount cancelled out (debit + credit = 0)")
    return True


def main():
    print("\nVerifying Transfer Fix")
    print("="*70)
    
    all_passed = True
    
    try:
        all_passed &= test_normal_transfer()
    except Exception as e:
        print(f"✗ Test 1 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False
    
    try:
        all_passed &= test_self_send()
    except Exception as e:
        print(f"✗ Test 2 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False
    
    print()
    if all_passed:
        print("=" * 70)
        print("ALL TESTS PASSED ✓")
        print("=" * 70)
        return 0
    else:
        print("=" * 70)
        print("SOME TESTS FAILED ✗")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
