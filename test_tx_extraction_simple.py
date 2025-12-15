#!/usr/bin/env python3
"""
Simple test to verify transaction field extraction from canonical Tx structure.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))


def test_recipient_extraction():
    """Test that recipient is correctly extracted from canonical Tx.unsigned.payload structure."""
    print("\n=== Testing recipient extraction from canonical Tx ===")
    
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    from execution.runtime.transfers import _get, _as_bytes
    
    # Create canonical Tx
    sender = b"\x01" * 32
    recipient = b"\x02" * 32
    
    unsigned = UnsignedTx(
        chain_id=1337,
        nonce=0,
        gas_price=1,
        gas_limit=21000,
        sender=sender,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient, amount=1000, data=b""),
        access_list=(),
    )
    
    sig = PqSignature(alg_id=1, pubkey=b"\x03" * 1952, sig=b"\x04" * 2420)
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Test extraction (mimicking apply_transfer logic)
    to = _get(tx, "to", "recipient", "to_address")
    if to is None:
        unsigned = _get(tx, "unsigned")
        if unsigned is not None:
            payload = _get(unsigned, "payload")
            if payload is not None:
                to = _get(payload, "to", "recipient")
    
    to = _as_bytes(to, expect_len=None)
    
    print(f"  Expected: {recipient.hex()}")
    print(f"  Got:      {to.hex()}")
    
    assert to == recipient, f"Recipient extraction failed: expected {recipient.hex()}, got {to.hex()}"
    print("✓ Recipient extraction works correctly")
    
    # Test amount extraction
    amount = _get(tx, "value", "amount")
    if amount is None:
        unsigned = _get(tx, "unsigned")
        if unsigned is not None:
            payload = _get(unsigned, "payload")
            if payload is not None:
                amount = _get(payload, "amount", "value")
    
    from execution.runtime.transfers import _as_int
    amount = _as_int(amount, default=0)
    
    print(f"  Amount expected: 1000")
    print(f"  Amount got:      {amount}")
    
    assert amount == 1000, f"Amount extraction failed: expected 1000, got {amount}"
    print("✓ Amount extraction works correctly")
    
    return True


def test_gas_extraction():
    """Test that gas_price and gas_limit are correctly extracted from UnsignedTx."""
    print("\n=== Testing gas extraction from canonical Tx ===")
    
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    
    # Create canonical Tx
    sender = b"\x01" * 32
    recipient = b"\x02" * 32
    
    unsigned = UnsignedTx(
        chain_id=1337,
        nonce=5,
        gas_price=123,
        gas_limit=50000,
        sender=sender,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient, amount=2000, data=b""),
        access_list=(),
    )
    
    sig = PqSignature(alg_id=1, pubkey=b"\x03" * 1952, sig=b"\x04" * 2420)
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Test extraction (mimicking _execute_transactions logic)
    nonce = 0
    gas_price = 1
    if hasattr(tx, "unsigned"):
        nonce = getattr(tx.unsigned, "nonce", 0)
        gas_price = getattr(tx.unsigned, "gas_price", 1)
    
    print(f"  Nonce expected: 5, got: {nonce}")
    print(f"  Gas price expected: 123, got: {gas_price}")
    
    assert nonce == 5, f"Nonce extraction failed: expected 5, got {nonce}"
    assert gas_price == 123, f"Gas price extraction failed: expected 123, got {gas_price}"
    
    print("✓ Gas extraction works correctly")
    
    # Test gas_limit extraction from apply_transfer perspective
    from execution.runtime.transfers import _get, _as_int
    
    gas_limit = _get(tx, "gas", "gas_limit", "gasLimit")
    if gas_limit is None or gas_limit == 0:
        unsigned = _get(tx, "unsigned")
        if unsigned is not None:
            gas_limit = _get(unsigned, "gas_limit", "gasLimit")
    gas_limit = _as_int(gas_limit, default=0)
    
    print(f"  Gas limit expected: 50000, got: {gas_limit}")
    assert gas_limit == 50000, f"Gas limit extraction failed: expected 50000, got {gas_limit}"
    
    print("✓ Gas limit extraction works correctly")
    
    return True


def test_apply_transfer_with_canonical_tx():
    """Test apply_transfer with a canonical Tx object."""
    print("\n=== Testing apply_transfer with canonical Tx ===")
    
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    from execution.runtime.transfers import apply_transfer
    from execution.runtime.env import BlockEnv, TxEnv
    
    # Create canonical Tx
    sender = b"\x01" * 32
    recipient = b"\x02" * 32
    transfer_amount = 1_000_000_000  # 1 ANM
    
    unsigned = UnsignedTx(
        chain_id=1337,
        nonce=0,
        gas_price=1,
        gas_limit=21000,
        sender=sender,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient, amount=transfer_amount, data=b""),
        access_list=(),
    )
    
    sig = PqSignature(alg_id=1, pubkey=b"\x03" * 1952, sig=b"\x04" * 2420)
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Create mock state
    class MockState:
        def __init__(self):
            self.balances = {sender: 10_000_000_000, recipient: 0}
            self.nonces = {sender: 0, recipient: 0}
        
        def get_balance(self, addr):
            return self.balances.get(addr, 0)
        
        def set_balance(self, addr, value):
            self.balances[addr] = value
        
        def get_nonce(self, addr):
            return self.nonces.get(addr, 0)
        
        def set_nonce(self, addr, value):
            self.nonces[addr] = value
    
    state = MockState()
    
    # Create environments
    block_env = BlockEnv(
        height=1,
        timestamp=1700000000,
        coinbase=b"\x00" * 32,
        chain_id=1337,
    )
    
    tx_env = TxEnv(
        sender=sender,
        chain_id=1337,
        nonce=0,
        gas_price=1,
    )
    
    # Execute transfer
    result = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    
    # Check results
    print(f"  Status: {'SUCCESS' if result.is_success else 'FAILED'}")
    print(f"  Gas used: {result.gas_used}")
    print(f"  Recipient balance: {state.get_balance(recipient)}")
    print(f"  Sender nonce: {state.get_nonce(sender)}")
    
    assert result.is_success, "Transfer should succeed"
    assert state.get_balance(recipient) == transfer_amount, f"Recipient should have {transfer_amount}"
    assert state.get_nonce(sender) == 1, "Sender nonce should be 1"
    
    print("✓ apply_transfer works correctly with canonical Tx")
    
    return True


if __name__ == "__main__":
    try:
        success = True
        success = test_recipient_extraction() and success
        success = test_gas_extraction() and success
        success = test_apply_transfer_with_canonical_tx() and success
        
        print("\n" + "="*70)
        if success:
            print("ALL TESTS PASSED ✓")
        else:
            print("SOME TESTS FAILED ✗")
        print("="*70)
        
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
