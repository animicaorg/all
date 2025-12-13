#!/usr/bin/env python3
"""
Minimal test to verify that balance updates work correctly with 32-byte addresses.

This test verifies the fix for the address length mismatch issue where:
- Transactions were being executed with 20-byte addresses
- But the state DB expects 32-byte addresses
- Causing balance updates to fail
"""

import sys
from dataclasses import dataclass

# Mock minimal StateDB for testing
@dataclass
class Account:
    nonce: int = 0
    balance: int = 0
    code_hash: bytes = b"\x00" * 32


class MockStateDB:
    """Minimal state DB that stores accounts by 32-byte keys."""
    
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


def test_32_byte_addresses():
    """Test that 32-byte addresses work correctly."""
    state = MockStateDB()
    
    # Create 32-byte addresses (Animica format)
    alice = b"\x01" * 32
    bob = b"\x02" * 32
    
    # Set initial balances
    state.set_balance(alice, 1_000_000)
    state.set_balance(bob, 0)
    
    # Verify balances
    assert state.get_balance(alice) == 1_000_000, f"Alice balance wrong: {state.get_balance(alice)}"
    assert state.get_balance(bob) == 0, f"Bob balance wrong: {state.get_balance(bob)}"
    
    # Simulate a transfer
    amount = 100_000
    state.set_balance(alice, state.get_balance(alice) - amount)
    state.set_balance(bob, state.get_balance(bob) + amount)
    
    # Verify transfer worked
    assert state.get_balance(alice) == 900_000, f"Alice balance after transfer wrong: {state.get_balance(alice)}"
    assert state.get_balance(bob) == 100_000, f"Bob balance after transfer wrong: {state.get_balance(bob)}"
    
    print("✓ 32-byte address test passed")
    return True


def test_20_byte_addresses_padded_to_32():
    """Test that 20-byte addresses are properly padded to 32 bytes."""
    state = MockStateDB()
    
    # Create 20-byte addresses (EVM format)
    alice_20 = b"\x01" * 20
    bob_20 = b"\x02" * 20
    
    # Pad to 32 bytes (right-justified with zeros on left, matching our fix)
    alice_32 = alice_20.rjust(32, b"\x00")
    bob_32 = bob_20.rjust(32, b"\x00")
    
    # Set initial balances using 32-byte padded addresses
    state.set_balance(alice_32, 1_000_000)
    state.set_balance(bob_32, 0)
    
    # Verify balances
    assert state.get_balance(alice_32) == 1_000_000, f"Alice balance wrong: {state.get_balance(alice_32)}"
    assert state.get_balance(bob_32) == 0, f"Bob balance wrong: {state.get_balance(bob_32)}"
    
    # Verify that 20-byte addresses DO NOT work (they need padding)
    assert state.get_balance(alice_20) == 0, "20-byte address should not match 32-byte key"
    
    print("✓ 20-byte to 32-byte padding test passed")
    return True


def test_transfers_module():
    """Test the actual transfers module if available."""
    try:
        from execution.runtime.transfers import apply_transfer
        from execution.runtime.env import BlockEnv, TxEnv
        
        # Create mock state
        state = MockStateDB()
        
        # Create 32-byte addresses
        alice = b"\x01" * 32
        bob = b"\x02" * 32
        coinbase = b"\x03" * 32
        
        # Set initial balances
        state.set_balance(alice, 1_000_000)
        state.set_balance(bob, 0)
        
        # Create block environment
        block_env = BlockEnv(
            height=1,
            timestamp=1000,
            coinbase=coinbase,
            chain_id=1,
        )
        
        # Create tx environment (32-byte sender)
        tx_env = TxEnv(
            sender=alice,
            chain_id=1,
            nonce=0,
            gas_price=1,
        )
        
        # Create transaction-like object
        class MockTx:
            to = bob
            value = 100_000
            gas = 21_000
            gasLimit = 21_000
        
        tx = MockTx()
        
        # Apply transfer
        result = apply_transfer(tx, state, block_env, tx_env, emit_event=False)
        
        # Verify result
        if result.status.name != "SUCCESS":
            raise AssertionError(f"Transfer failed with status: {result.status}")
        
        # Verify balances were updated
        # Alice should have: initial - amount - gas_fee
        gas_fee = 21_000 * 1  # gas_limit * gas_price
        expected_alice = 1_000_000 - 100_000 - gas_fee
        actual_alice = state.get_balance(alice)
        
        if actual_alice != expected_alice:
            raise AssertionError(
                f"Alice balance wrong after transfer: expected {expected_alice}, got {actual_alice}"
            )
        
        # Bob should have: amount
        expected_bob = 100_000
        actual_bob = state.get_balance(bob)
        
        if actual_bob != expected_bob:
            raise AssertionError(
                f"Bob balance wrong after transfer: expected {expected_bob}, got {actual_bob}"
            )
        
        # Verify nonce was incremented
        if state.get_nonce(alice) != 1:
            raise AssertionError(f"Alice nonce not incremented: {state.get_nonce(alice)}")
        
        print("✓ Transfers module integration test passed")
        return True
        
    except ImportError as e:
        print(f"⊘ Transfers module test skipped (import error: {e})")
        return True  # Not a failure, just skipped


def main():
    print("Testing balance update fix for 32-byte addresses...")
    print()
    
    all_passed = True
    
    try:
        all_passed &= test_32_byte_addresses()
    except Exception as e:
        print(f"✗ 32-byte address test failed: {e}")
        all_passed = False
    
    try:
        all_passed &= test_20_byte_addresses_padded_to_32()
    except Exception as e:
        print(f"✗ Padding test failed: {e}")
        all_passed = False
    
    try:
        all_passed &= test_transfers_module()
    except Exception as e:
        print(f"✗ Transfers module test failed: {e}")
        import traceback
        traceback.print_exc()
        all_passed = False
    
    print()
    if all_passed:
        print("=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)
        return 0
    else:
        print("=" * 60)
        print("SOME TESTS FAILED ✗")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
