#!/usr/bin/env python3
"""
Reproduce double debit issue as described in the problem statement.

This test:
1. Sets up a mock chain environment  
2. Sends a transaction with ANIMICA_DEBUG_BALANCE=1
3. Mines a block
4. Checks balance mutation logs to identify TWO debit callsites
"""

import os
import sys
from types import SimpleNamespace

# Enable debug logging BEFORE importing modules
os.environ["ANIMICA_DEBUG_BALANCE"] = "1"
os.environ["ANIMICA_DEBUG_TX"] = "1"

from execution.runtime.transfers import apply_transfer
from execution.state.apply_balance import get_debug_balance_events, reset_debug_balance_events
from execution.types.status import TxStatus


class MockState:
    """Mock state DB that tracks all balance operations."""
    
    def __init__(self, balances: dict[bytes, int], nonces: dict[bytes, int] | None = None) -> None:
        self._balances = dict(balances)
        self._nonces = dict(nonces or {})
        self._operations = []

    def get_balance(self, addr: bytes) -> int:
        return int(self._balances.get(addr, 0))

    def set_balance(self, addr: bytes, value: int) -> None:
        old = self._balances.get(addr, 0)
        self._balances[addr] = int(value)
        self._operations.append({
            "op": "set_balance",
            "addr": addr.hex(),
            "old": old,
            "new": int(value),
            "delta": int(value) - old
        })

    def get_nonce(self, addr: bytes) -> int:
        return int(self._nonces.get(addr, 0))

    def set_nonce(self, addr: bytes, value: int) -> None:
        self._nonces[addr] = int(value)

    def ensure_account(self, addr: bytes) -> None:
        self._balances.setdefault(addr, 0)


def test_reproduce_double_debit():
    """
    Reproduce the double debit issue.
    
    Expected (BUGGY behavior if issue exists):
    - Two negative deltas for sender with same tx_hash
    - Logs show TWO different callsites debiting sender
    
    Expected (FIXED behavior):
    - One negative delta for sender
    - Receiver has one positive delta
    """
    print("=" * 80)
    print("REPRODUCING DOUBLE DEBIT ISSUE")
    print("=" * 80)
    
    # Setup
    sender = b"\x11" * 32
    recipient = b"\x22" * 32
    coinbase = b"\x33" * 32
    treasury = b"\x44" * 32
    tx_hash = "0xtest-double-debit"
    
    initial_sender = 300_000_000_000  # 300 ANM in base units
    value = 10_000_000_000  # 10 ANM
    gas_limit = 21_000
    gas_price = 1
    fee = gas_limit * gas_price  # 21,000 base units
    
    print(f"\nInitial Setup:")
    print(f"  Sender:    {sender.hex()[:16]}... = {initial_sender / 1e9:.2f} ANM")
    print(f"  Recipient: {recipient.hex()[:16]}... = 0 ANM")
    print(f"  Value:     {value / 1e9:.2f} ANM")
    print(f"  Fee:       {fee / 1e9:.6f} ANM")
    print(f"  Expected sender debit: {(value + fee) / 1e9:.6f} ANM")
    
    # Create state
    state = MockState(
        {sender: initial_sender, recipient: 0, coinbase: 0, treasury: 0},
        {sender: 0}
    )
    
    # Create transaction
    tx = {
        "to": recipient,
        "amount": value,
        "gas_limit": gas_limit,
        "nonce": 0,
        "hash": tx_hash
    }
    
    # Create environments
    block_env = SimpleNamespace(
        coinbase=coinbase,
        treasury=treasury,
        height=1
    )
    tx_env = SimpleNamespace(
        sender=sender,
        gas_price=gas_price,
        base_price=0  # All goes to coinbase as tip
    )
    
    # Reset debug events
    reset_debug_balance_events()
    
    # Apply transaction (this is what happens during block application)
    print(f"\nApplying transaction {tx_hash}...")
    result = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    
    # Check result
    print(f"\nTransaction Result:")
    print(f"  Status:    {result.status.name}")
    print(f"  Gas Used:  {result.gas_used}")
    
    # Check final balances
    final_sender = state.get_balance(sender)
    final_recipient = state.get_balance(recipient)
    final_coinbase = state.get_balance(coinbase)
    
    print(f"\nFinal Balances:")
    print(f"  Sender:    {final_sender / 1e9:.6f} ANM (change: {(final_sender - initial_sender) / 1e9:.6f} ANM)")
    print(f"  Recipient: {final_recipient / 1e9:.6f} ANM (change: +{final_recipient / 1e9:.6f} ANM)")
    print(f"  Coinbase:  {final_coinbase / 1e9:.6f} ANM (change: +{final_coinbase / 1e9:.6f} ANM)")
    
    # Get debug balance events
    events = get_debug_balance_events(tx_hash=tx_hash)
    
    print(f"\nBalance Mutation Events ({len(events)} total):")
    print("-" * 80)
    
    sender_debits = []
    recipient_credits = []
    
    for i, evt in enumerate(events, 1):
        addr = evt.get("address", "")
        delta = int(evt.get("delta", 0))
        reason = evt.get("reason", "")
        site = evt.get("callsite", "")
        
        print(f"{i}. Address: {addr[:16]}...")
        print(f"   Delta:   {delta:+d} ({delta / 1e9:+.6f} ANM)")
        print(f"   Reason:  {reason}")
        print(f"   Site:    {site}")
        print()
        
        if addr == sender.hex() and delta < 0:
            sender_debits.append(evt)
        if addr == recipient.hex() and delta > 0:
            recipient_credits.append(evt)
    
    # Analyze results
    print("=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    
    print(f"\nSender negative deltas: {len(sender_debits)}")
    if sender_debits:
        total_sender_delta = sum(int(e.get("delta", 0)) for e in sender_debits)
        print(f"  Total: {total_sender_delta:+d} ({total_sender_delta / 1e9:+.6f} ANM)")
        print(f"  Expected: {-(value + fee):+d} ({-(value + fee) / 1e9:+.6f} ANM)")
        for i, e in enumerate(sender_debits, 1):
            print(f"  Debit #{i}: {e.get('delta')} from {e.get('reason')} at {e.get('callsite')}")
    
    print(f"\nRecipient positive deltas: {len(recipient_credits)}")
    if recipient_credits:
        total_recipient_delta = sum(int(e.get("delta", 0)) for e in recipient_credits)
        print(f"  Total: {total_recipient_delta:+d} ({total_recipient_delta / 1e9:+.6f} ANM)")
        print(f"  Expected: {value:+d} ({value / 1e9:+.6f} ANM)")
    
    # Verdict
    print("\n" + "=" * 80)
    if len(sender_debits) > 1:
        print("❌ BUG DETECTED: Sender has MULTIPLE debits for same tx!")
        print(f"   Found {len(sender_debits)} debits from different callsites:")
        for i, e in enumerate(sender_debits, 1):
            print(f"   {i}. {e.get('reason')} at {e.get('callsite')}")
        return False
    elif len(sender_debits) == 1:
        print("✅ CORRECT: Sender has exactly ONE debit")
        return True
    else:
        print("⚠️  UNEXPECTED: Sender has NO debits!")
        return False


def main():
    try:
        success = test_reproduce_double_debit()
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
