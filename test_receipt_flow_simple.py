#!/usr/bin/env python3
"""
Simple test to verify receipt persistence without RPC complexity.

This test validates:
1. Blocks with receipts are persisted via append_canonical_block
2. Receipts can be retrieved by tx_hash
3. Receipt data (status, gas_used) is correct
"""

import sys
import tempfile
import os

def test_receipt_flow():
    """Test the complete receipt persistence and retrieval flow."""
    from core.db.sqlite import SQLiteKV
    from core.db.block_db import BlockDB
    from core.types.block import Block
    from core.types.header import Header
    from core.types.receipt import Receipt, ReceiptStatus, Log
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    
    print("\n" + "="*70)
    print("TEST: Receipt Persistence and Retrieval")
    print("="*70)
    
    # Create temp db
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        kv = SQLiteKV(f'sqlite:///{db_path}')
        bdb = BlockDB(kv)
        
        # Step 1: Create a transaction
        print("\n1. Creating transaction")
        sender = b'\x01' * 32
        recipient = b'\x02' * 32
        transfer_amount = 1_000_000_000  # 1 ANM in nANM
        
        unsigned = UnsignedTx(
            chain_id=1337,
            nonce=0,
            gas_price=1,
            gas_limit=21000,
            sender=sender,
            kind=TxKind.TRANSFER,
            payload=TxTransfer(to=recipient, amount=transfer_amount, data=b''),
            access_list=()
        )
        sig = PqSignature(alg_id=0x31, pubkey=b'\x03' * 32, sig=b'\x04' * 64)
        tx = Tx(unsigned=unsigned, sigs=(sig,))
        tx_hash = tx.hash()
        
        print(f"   TX hash: 0x{tx_hash.hex()[:16]}...")
        print(f"   Transfer: {transfer_amount:,} nANM")
        
        # Step 2: Create a receipt (simulating successful execution)
        print("\n2. Creating receipt")
        receipt = Receipt(
            status=ReceiptStatus.SUCCESS,
            gas_used=21000,
            logs=()
        )
        print(f"   Status: {receipt.status.name}")
        print(f"   Gas used: {receipt.gas_used:,}")
        
        # Step 3: Create block with transaction and receipt
        print("\n3. Creating block")
        block_temp = Block(
            header=Header(
                v=1, chainId=1337, height=1, parentHash=b'\x00' * 32,
                timestamp=1000, stateRoot=b'\x00' * 32, txsRoot=b'\x00' * 32,
                receiptsRoot=b'\x00' * 32, proofsRoot=b'\x00' * 32, daRoot=b'\x00' * 32,
                mixSeed=b'\x00' * 32, poiesPolicyRoot=b'\x00' * 32, pqAlgPolicyRoot=b'\x00' * 32,
                thetaMicro=0, nonce=0, extra=b''
            ),
            txs=(tx,),
            proofs=(),
            receipts=(receipt,)
        )
        
        # Compute correct roots
        txs_root = block_temp.txs_root()
        receipts_root = block_temp.receipts_root()
        
        header = Header(
            v=1, chainId=1337, height=1, parentHash=b'\x00' * 32,
            timestamp=1000, stateRoot=b'\x00' * 32, txsRoot=txs_root,
            receiptsRoot=receipts_root, proofsRoot=b'\x00' * 32, daRoot=b'\x00' * 32,
            mixSeed=b'\x00' * 32, poiesPolicyRoot=b'\x00' * 32, pqAlgPolicyRoot=b'\x00' * 32,
            thetaMicro=0, nonce=0, extra=b''
        )
        
        block = Block(header=header, txs=(tx,), proofs=(), receipts=(receipt,))
        print(f"   Block height: {header.height}")
        print(f"   Block txs: {len(block.txs)}")
        print(f"   Block receipts: {len(block.receipts) if block.receipts else 0}")
        
        # Step 4: Persist block (this should index the receipt)
        print("\n4. Persisting block with receipt indexing")
        bdb.append_canonical_block(1, block)
        print("   ✓ Block persisted")
        
        # Step 5: Retrieve the block to verify it was stored
        print("\n5. Verifying block was stored")
        stored_block = bdb.get_block_by_height(1)
        assert stored_block is not None, "Block should be stored"
        assert len(stored_block.txs) == 1, "Block should have 1 transaction"
        assert stored_block.receipts is not None, "Block should have receipts"
        assert len(stored_block.receipts) == 1, "Block should have 1 receipt"
        print("   ✓ Block retrieved from storage")
        print(f"   ✓ Block has {len(stored_block.txs)} transaction(s)")
        print(f"   ✓ Block has {len(stored_block.receipts)} receipt(s)")
        
        # Step 6: Look up receipt by transaction hash
        print("\n6. Looking up receipt by tx_hash")
        result = bdb.get_receipt_by_tx_hash(tx_hash)
        assert result is not None, "Receipt should be found"
        
        height, idx, block_hash, retrieved_receipt = result
        print(f"   ✓ Receipt found!")
        print(f"   Height: {height}")
        print(f"   Index: {idx}")
        print(f"   Block hash: 0x{block_hash.hex()[:16]}...")
        print(f"   Status: {retrieved_receipt.status.name}")
        print(f"   Gas used: {retrieved_receipt.gas_used:,}")
        
        # Step 7: Verify receipt data
        print("\n7. Verifying receipt data")
        assert height == 1, f"Wrong height: {height}"
        assert idx == 0, f"Wrong index: {idx}"
        assert retrieved_receipt.status == ReceiptStatus.SUCCESS, f"Wrong status: {retrieved_receipt.status}"
        assert retrieved_receipt.gas_used == 21000, f"Wrong gas_used: {retrieved_receipt.gas_used}"
        print("   ✓ Receipt height matches")
        print("   ✓ Receipt index matches")
        print("   ✓ Receipt status is SUCCESS")
        print("   ✓ Receipt gas_used matches")
        
        # Step 8: Test lookup of non-existent transaction
        print("\n8. Testing lookup of non-existent transaction")
        fake_hash = b'\xff' * 32
        result = bdb.get_receipt_by_tx_hash(fake_hash)
        assert result is None, "Should return None for non-existent tx"
        print("   ✓ Returns None for non-existent transaction")
        
        print("\n" + "="*70)
        print("✓ ALL TESTS PASSED")
        print("="*70)
        print("\nSummary:")
        print("  • Block with receipts persisted correctly")
        print("  • Receipt indexed by tx_hash")
        print("  • Receipt retrievable via get_receipt_by_tx_hash")
        print("  • Receipt data (status, gas_used) correct")
        print("  • Non-existent tx returns None")
        
        return True
        
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        os.unlink(db_path)


if __name__ == "__main__":
    success = test_receipt_flow()
    sys.exit(0 if success else 1)
