#!/usr/bin/env python3
"""
Test to verify transaction execution fixes for canonical Tx structure.

This test validates that:
1. Sender extraction works with bech32 addresses
2. Recipient/amount extraction works with canonical Tx.unsigned.payload structure
3. Gas extraction works with canonical Tx.unsigned.gas_price/gas_limit structure
4. State changes (balance transfers, nonce increments) are properly applied
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(__file__))


def test_sender_extraction_with_bech32():
    """Test that _as_bytes32_addr correctly handles bech32 addresses."""
    print("\n=== Testing sender extraction with bech32 addresses ===")
    
    from rpc.methods.miner import _as_bytes32_addr
    
    # Test with bech32 address
    try:
        from pq.py.keygen import keygen_sig
        kp = keygen_sig("dilithium3")
        bech32_addr = kp.address
        
        # This should NOT raise an exception or return ZERO32
        addr_bytes = _as_bytes32_addr(bech32_addr)
        
        assert len(addr_bytes) == 32, f"Address should be 32 bytes, got {len(addr_bytes)}"
        assert addr_bytes != b"\x00" * 32, "Address should not be zero"
        
        print(f"✓ Bech32 address decoded: {bech32_addr[:20]}... -> {addr_bytes.hex()[:16]}...")
        
        # Test with hex address
        hex_addr = "0x" + (b"\x01" * 32).hex()
        addr_bytes2 = _as_bytes32_addr(hex_addr)
        assert len(addr_bytes2) == 32
        print(f"✓ Hex address decoded: {hex_addr[:20]}... -> {addr_bytes2.hex()[:16]}...")
        
        # Test with raw bytes
        raw_bytes = b"\x02" * 32
        addr_bytes3 = _as_bytes32_addr(raw_bytes)
        assert addr_bytes3 == raw_bytes
        print(f"✓ Raw bytes passed through: {raw_bytes.hex()[:16]}...")
        
        return True
    except Exception as e:
        print(f"✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_canonical_tx_structure_extraction():
    """Test extraction of recipient, amount, gas from canonical Tx structure."""
    print("\n=== Testing canonical Tx structure extraction ===")
    
    try:
        from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
        from execution.runtime.transfers import apply_transfer
        from execution.runtime.env import BlockEnv, TxEnv
        from pq.py.keygen import keygen_sig
        
        # Generate sender and recipient
        sender_kp = keygen_sig("dilithium3")
        recipient_kp = keygen_sig("dilithium3")
        
        # Decode addresses to bytes
        from pq.py.address import decode_address
        sender_record = decode_address(sender_kp.address)
        recipient_record = decode_address(recipient_kp.address)
        
        sender_bytes = bytes(sender_record.digest) if isinstance(sender_record.digest, list) else sender_record.digest
        sender_bytes = sender_bytes[:32].ljust(32, b"\x00")
        
        recipient_bytes = bytes(recipient_record.digest) if isinstance(recipient_record.digest, list) else recipient_record.digest
        recipient_bytes = recipient_bytes[:32].ljust(32, b"\x00")
        
        # Create canonical Tx structure
        transfer_amount = 1_000_000_000  # 1 ANM
        unsigned = UnsignedTx(
            chain_id=1337,
            nonce=0,
            gas_price=1,
            gas_limit=21000,
            sender=sender_bytes,
            kind=TxKind.TRANSFER,
            payload=TxTransfer(to=recipient_bytes, amount=transfer_amount, data=b""),
            access_list=(),
        )
        
        # Dummy signature
        sig = PqSignature(alg_id=1, pubkey=b"\x03" * 1952, sig=b"\x04" * 2420)
        tx = Tx(unsigned=unsigned, sigs=(sig,))
        
        print(f"✓ Created canonical Tx with:")
        print(f"  - Sender: {sender_bytes.hex()[:16]}...")
        print(f"  - Recipient: {recipient_bytes.hex()[:16]}...")
        print(f"  - Amount: {transfer_amount}")
        print(f"  - Gas price: {unsigned.gas_price}")
        print(f"  - Gas limit: {unsigned.gas_limit}")
        
        # Create mock state with initial balances
        class MockState:
            def __init__(self):
                self.balances = {sender_bytes: 10_000_000_000, recipient_bytes: 0}
                self.nonces = {sender_bytes: 0, recipient_bytes: 0}
            
            def get_balance(self, addr):
                return self.balances.get(addr, 0)
            
            def set_balance(self, addr, value):
                self.balances[addr] = value
            
            def get_nonce(self, addr):
                return self.nonces.get(addr, 0)
            
            def set_nonce(self, addr, value):
                self.nonces[addr] = value
        
        state = MockState()
        
        # Create block and tx environments
        block_env = BlockEnv(
            height=1,
            timestamp=1700000000,
            coinbase=b"\x00" * 32,
            chain_id=1337,
        )
        
        tx_env = TxEnv(
            sender=sender_bytes,
            chain_id=1337,
            nonce=0,
            gas_price=1,
        )
        
        # Execute transfer
        result = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
        
        print(f"\n✓ Transfer executed:")
        print(f"  - Status: {'SUCCESS' if result.is_success else 'FAILED'}")
        print(f"  - Gas used: {result.gas_used}")
        print(f"  - Logs: {len(result.logs or [])}")
        
        # Verify state changes
        sender_balance = state.get_balance(sender_bytes)
        recipient_balance = state.get_balance(recipient_bytes)
        sender_nonce = state.get_nonce(sender_bytes)
        
        print(f"\n✓ State changes:")
        print(f"  - Sender balance: {10_000_000_000} -> {sender_balance}")
        print(f"  - Recipient balance: {0} -> {recipient_balance}")
        print(f"  - Sender nonce: {0} -> {sender_nonce}")
        
        # Assertions
        assert result.is_success, "Transfer should succeed"
        assert recipient_balance == transfer_amount, f"Recipient should have {transfer_amount}, got {recipient_balance}"
        assert sender_nonce == 1, f"Sender nonce should be 1, got {sender_nonce}"
        
        expected_sender_deduction = transfer_amount + (21000 * 1)  # amount + gas_fee
        expected_sender_balance = 10_000_000_000 - expected_sender_deduction
        assert sender_balance == expected_sender_balance, f"Sender balance mismatch: expected {expected_sender_balance}, got {sender_balance}"
        
        print("\n✓ All assertions passed!")
        return True
        
    except Exception as e:
        print(f"\n✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_execute_transactions_integration():
    """Test _execute_transactions with canonical Tx objects."""
    print("\n=== Testing _execute_transactions integration ===")
    
    try:
        from rpc.methods.miner import _execute_transactions
        from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
        from execution.runtime.env import BlockEnv
        from pq.py.keygen import keygen_sig
        from pq.py.address import decode_address
        import logging
        
        # Set up logging
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger("test")
        
        # Generate addresses
        sender_kp = keygen_sig("dilithium3")
        recipient_kp = keygen_sig("dilithium3")
        
        sender_record = decode_address(sender_kp.address)
        recipient_record = decode_address(recipient_kp.address)
        
        sender_bytes = bytes(sender_record.digest) if isinstance(sender_record.digest, list) else sender_record.digest
        sender_bytes = sender_bytes[:32].ljust(32, b"\x00")
        
        recipient_bytes = bytes(recipient_record.digest) if isinstance(recipient_record.digest, list) else recipient_record.digest
        recipient_bytes = recipient_bytes[:32].ljust(32, b"\x00")
        
        # Create canonical Tx
        transfer_amount = 500_000_000  # 0.5 ANM
        unsigned = UnsignedTx(
            chain_id=1337,
            nonce=0,
            gas_price=2,
            gas_limit=21000,
            sender=sender_bytes,
            kind=TxKind.TRANSFER,
            payload=TxTransfer(to=recipient_bytes, amount=transfer_amount, data=b""),
            access_list=(),
        )
        
        sig = PqSignature(alg_id=1, pubkey=b"\x03" * 1952, sig=b"\x04" * 2420)
        tx = Tx(unsigned=unsigned, sigs=(sig,))
        
        # Create mock state
        class MockState:
            def __init__(self):
                self.balances = {sender_bytes: 5_000_000_000, recipient_bytes: 0}
                self.nonces = {sender_bytes: 0, recipient_bytes: 0}
            
            def get_balance(self, addr):
                return self.balances.get(addr, 0)
            
            def set_balance(self, addr, value):
                self.balances[addr] = value
            
            def get_nonce(self, addr):
                return self.nonces.get(addr, 0)
            
            def set_nonce(self, addr, value):
                self.nonces[addr] = value
        
        state = MockState()
        
        # Create block environment
        block_env = BlockEnv(
            height=10,
            timestamp=1700000000,
            coinbase=b"\x00" * 32,
            chain_id=1337,
        )
        
        # Execute transactions
        receipts = _execute_transactions(
            txs=[tx],
            state_db=state,
            block_env=block_env,
            logger=logger,
        )
        
        print(f"\n✓ Executed {len(receipts)} transaction(s)")
        print(f"  - Receipt status: {receipts[0]['status']}")
        print(f"  - Gas used: {receipts[0]['gasUsed']}")
        
        # Verify state changes
        recipient_balance = state.get_balance(recipient_bytes)
        sender_nonce = state.get_nonce(sender_bytes)
        
        print(f"\n✓ State after execution:")
        print(f"  - Recipient balance: {recipient_balance}")
        print(f"  - Sender nonce: {sender_nonce}")
        
        # Assertions
        assert receipts[0]["status"] == 1, "Transaction should succeed"
        assert recipient_balance == transfer_amount, f"Recipient should have {transfer_amount}, got {recipient_balance}"
        assert sender_nonce == 1, f"Sender nonce should be 1, got {sender_nonce}"
        
        print("\n✓ All assertions passed!")
        return True
        
    except Exception as e:
        print(f"\n✗ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = True
    
    try:
        success = test_sender_extraction_with_bech32() and success
        success = test_canonical_tx_structure_extraction() and success
        success = test_execute_transactions_integration() and success
        
        print("\n" + "="*70)
        if success:
            print("ALL TESTS PASSED ✓")
        else:
            print("SOME TESTS FAILED ✗")
        print("="*70)
        
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ TEST SUITE FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
