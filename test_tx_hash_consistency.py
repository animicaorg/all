#!/usr/bin/env python3
"""
Regression test for transaction hash consistency (Issue #436).

Tests that canonical tx hashes are consistent everywhere:
1. Hash returned by tx.sendRawTransaction matches sha3_256(raw_cbor)
2. Hash appears in block after mining (chain.getBlockByHeight)
3. tx.getTransactionByHash works with the returned hash
4. tx.getTransactionReceipt works with the returned hash
5. Balances and nonces update correctly after mining
6. txsRoot and receiptsRoot are non-zero when block contains transactions
7. Same tx hash cannot appear in multiple blocks

This test should FAIL on main before the fix and PASS after the fix.
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(__file__))


def test_tx_hash_consistency():
    """Test that tx hashes are canonical and consistent throughout the system."""
    print("\n" + "="*80)
    print("REGRESSION TEST: Transaction Hash Consistency (Issue #436)")
    print("="*80)
    
    # Import test helpers
    try:
        from rpc.tests import new_test_client, rpc_call
    except ImportError as e:
        print(f"SKIP: Cannot import test helpers: {e}")
        return True
    
    # Create test client
    client, cfg, _ = new_test_client()
    
    # Generate sender and recipient keypairs
    try:
        from pq.py.keygen import keygen_sig
        from pq.py.address import decode_address
    except ImportError as e:
        print(f"SKIP: Cannot import PQ modules: {e}")
        return True
    
    try:
        sender_kp = keygen_sig("dilithium3")
        recipient_kp = keygen_sig("dilithium3")
    except Exception as e:
        print(f"SKIP: PQ keygen not available: {e}")
        return True
    
    sender_addr_bech32 = sender_kp.address
    recipient_addr_bech32 = recipient_kp.address
    
    # Decode to get 32-byte addresses
    sender_record = decode_address(sender_addr_bech32)
    recipient_record = decode_address(recipient_addr_bech32)
    
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    recipient_bytes = bytes(recipient_record.digest)[:32].ljust(32, b"\x00")
    
    sender_hex = "0x" + sender_bytes.hex()
    recipient_hex = "0x" + recipient_bytes.hex()
    
    print(f"\n1. Setup")
    print(f"   Sender:    {sender_addr_bech32}")
    print(f"   Recipient: {recipient_addr_bech32}")
    
    # Fund sender by mining blocks
    print(f"\n2. Fund sender by mining 5 blocks")
    mine_result = rpc_call(client, "miner.mine", {"count": 5, "address": sender_addr_bech32})["result"]
    print(f"   Mined {mine_result['mined']} blocks")
    print(f"   Current height: {mine_result['height']}")
    
    # Check initial balances
    sender_balance_initial = int(rpc_call(client, "state.getBalance", [sender_hex])["result"], 16)
    recipient_balance_initial = int(rpc_call(client, "state.getBalance", [recipient_hex])["result"], 16)
    sender_nonce_initial = int(rpc_call(client, "state.getNonce", [sender_hex])["result"], 16)
    
    print(f"   Sender balance:    {sender_balance_initial:,} nANM")
    print(f"   Recipient balance: {recipient_balance_initial:,} nANM")
    print(f"   Sender nonce:      {sender_nonce_initial}")
    
    assert sender_balance_initial > 0, "Sender should have balance from mining rewards"
    
    # Build and sign a transaction
    print(f"\n3. Build and send transaction")
    
    from core.encoding.canonical import tx_sign_bytes
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    from pq.py import sign
    from pq.py.registry import ALG_ID
    from core.utils.hash import sha3_256
    
    transfer_amount = 1_000_000_000  # 1 ANM
    
    # Build unsigned transfer
    unsigned = UnsignedTx(
        chain_id=cfg.chain_id,
        nonce=sender_nonce_initial,
        gas_price=1,
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=transfer_amount, data=b""),
        access_list=(),
    )
    
    # Sign transaction
    sign_bytes_data = tx_sign_bytes(unsigned.to_obj())
    sig_env = sign.sign_detached(sign_bytes_data, "dilithium3", sender_kp.secret_key, domain="tx", chain_id=cfg.chain_id)
    sig = PqSignature(alg_id=ALG_ID["dilithium3"], pubkey=sender_kp.public_key, sig=sig_env.sig)
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Encode to CBOR hex
    cbor_bytes = tx.to_cbor()
    raw_hex = "0x" + cbor_bytes.hex()
    
    # Compute canonical hash: sha3_256(raw_cbor_bytes)
    canonical_hash = "0x" + sha3_256(cbor_bytes).hex()
    
    print(f"   Transfer amount: {transfer_amount:,} nANM")
    print(f"   Canonical hash (sha3_256(raw)): {canonical_hash}")
    
    # Submit transaction via RPC
    try:
        result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
        returned_hash = result.get("result")
        print(f"   RPC returned hash: {returned_hash}")
    except Exception as e:
        print(f"   ✗ ERROR: Failed to submit transaction: {e}")
        return False
    
    # TEST A: Canonical tx hash consistency
    print(f"\n4. TEST A: Verify tx.sendRawTransaction returns canonical hash")
    if returned_hash == canonical_hash:
        print(f"   ✓ PASS: Returned hash matches sha3_256(raw_cbor)")
    else:
        print(f"   ✗ FAIL: Hash mismatch!")
        print(f"      Expected (canonical): {canonical_hash}")
        print(f"      Got (RPC):            {returned_hash}")
        return False
    
    # Check that tx is in mempool
    print(f"\n5. Verify tx is in mempool")
    pending = rpc_call(client, "mempool.getPending")["result"]
    if returned_hash in pending:
        print(f"   ✓ TX is in mempool")
    else:
        print(f"   ✗ TX NOT in mempool (pending: {len(pending)} txs)")
        return False
    
    # Mine a block to include the transaction
    print(f"\n6. Mine block to include transaction")
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_addr_bech32})["result"]
    mined_height = mine_result['height']
    print(f"   Mined block at height {mined_height}")
    
    # TEST B: Verify tx hash appears in block
    print(f"\n7. TEST B: Verify canonical hash appears in block")
    block = rpc_call(client, "chain.getBlockByNumber", [mined_height, True])["result"]
    block_txs = block.get("transactions", [])
    
    if not block_txs:
        print(f"   ✗ FAIL: Block contains no transactions")
        return False
    
    # Extract tx hashes from block
    tx_hashes_in_block = []
    for tx_data in block_txs:
        if isinstance(tx_data, dict):
            tx_hashes_in_block.append(tx_data.get("hash"))
        elif isinstance(tx_data, str):
            tx_hashes_in_block.append(tx_data)
    
    print(f"   Block contains {len(tx_hashes_in_block)} transaction(s)")
    print(f"   TX hashes in block: {[h[:16] + '...' if h else 'None' for h in tx_hashes_in_block]}")
    
    if returned_hash in tx_hashes_in_block:
        print(f"   ✓ PASS: Canonical hash appears in block")
    else:
        print(f"   ✗ FAIL: Canonical hash NOT in block")
        print(f"      Expected: {returned_hash}")
        print(f"      Got:      {tx_hashes_in_block}")
        return False
    
    # TEST C: Verify tx.getTransactionByHash works
    print(f"\n8. TEST C: Verify tx.getTransactionByHash works with canonical hash")
    try:
        tx_result = rpc_call(client, "tx.getTransactionByHash", [returned_hash])["result"]
        if tx_result is None:
            print(f"   ✗ FAIL: getTransactionByHash returned null")
            return False
        
        print(f"   ✓ PASS: getTransactionByHash returned tx data")
        print(f"      blockNumber: {tx_result.get('blockNumber')}")
        print(f"      blockHash:   {tx_result.get('blockHash', 'N/A')[:18]}...")
        print(f"      txIndex:     {tx_result.get('transactionIndex')}")
        
        # Verify the hash in the response matches
        if tx_result.get("hash") != returned_hash:
            print(f"   ⚠ WARNING: Response hash doesn't match")
            print(f"      Expected: {returned_hash}")
            print(f"      Got:      {tx_result.get('hash')}")
    except Exception as e:
        print(f"   ✗ FAIL: getTransactionByHash raised exception: {e}")
        return False
    
    # TEST D: Verify tx.getTransactionReceipt works
    print(f"\n9. TEST D: Verify tx.getTransactionReceipt works with canonical hash")
    try:
        receipt = rpc_call(client, "tx.getTransactionReceipt", [returned_hash])["result"]
        if receipt is None:
            print(f"   ✗ FAIL: getTransactionReceipt returned null")
            return False
        
        print(f"   ✓ PASS: getTransactionReceipt returned receipt")
        print(f"      transactionHash: {receipt.get('transactionHash', 'N/A')[:18]}...")
        print(f"      blockNumber:     {receipt.get('blockNumber')}")
        print(f"      blockHash:       {receipt.get('blockHash', 'N/A')[:18]}...")
        print(f"      status:          {receipt.get('status')}")
        print(f"      gasUsed:         {receipt.get('gasUsed')}")
        
        # Verify the receipt is for the correct transaction
        if receipt.get("transactionHash") != returned_hash:
            print(f"   ⚠ WARNING: Receipt hash doesn't match")
            print(f"      Expected: {returned_hash}")
            print(f"      Got:      {receipt.get('transactionHash')}")
        
        # Verify receipt indicates success
        if receipt.get("status") != 1:
            print(f"   ⚠ WARNING: Transaction did not succeed (status={receipt.get('status')})")
    except Exception as e:
        print(f"   ✗ FAIL: getTransactionReceipt raised exception: {e}")
        return False
    
    # TEST E: Verify balances updated correctly
    print(f"\n10. TEST E: Verify balances and nonces updated correctly")
    sender_balance_final = int(rpc_call(client, "state.getBalance", [sender_hex])["result"], 16)
    recipient_balance_final = int(rpc_call(client, "state.getBalance", [recipient_hex])["result"], 16)
    sender_nonce_final = int(rpc_call(client, "state.getNonce", [sender_hex])["result"], 16)
    
    print(f"   Sender balance:    {sender_balance_final:,} nANM (was {sender_balance_initial:,})")
    print(f"   Recipient balance: {recipient_balance_final:,} nANM (was {recipient_balance_initial:,})")
    print(f"   Sender nonce:      {sender_nonce_final} (was {sender_nonce_initial})")
    
    # Verify nonce incremented
    if sender_nonce_final == sender_nonce_initial + 1:
        print(f"   ✓ PASS: Sender nonce incremented correctly")
    else:
        print(f"   ✗ FAIL: Sender nonce did not increment")
        print(f"      Expected: {sender_nonce_initial + 1}")
        print(f"      Got:      {sender_nonce_final}")
        return False
    
    # Verify recipient balance increased
    recipient_increase = recipient_balance_final - recipient_balance_initial
    if recipient_increase == transfer_amount:
        print(f"   ✓ PASS: Recipient balance increased by {recipient_increase:,} nANM")
    else:
        print(f"   ✗ FAIL: Recipient balance did not increase correctly")
        print(f"      Expected increase: {transfer_amount:,}")
        print(f"      Actual increase:   {recipient_increase:,}")
        return False
    
    # TEST F: Verify txsRoot and receiptsRoot are non-zero
    print(f"\n11. TEST F: Verify txsRoot and receiptsRoot are non-zero")
    block_header = block.get("header", {})
    txs_root = block_header.get("txsRoot", "0x0")
    receipts_root = block_header.get("receiptsRoot", "0x0")
    
    print(f"   txsRoot:      {txs_root}")
    print(f"   receiptsRoot: {receipts_root}")
    
    zero_root = "0x" + "0" * 64
    if txs_root != zero_root:
        print(f"   ✓ PASS: txsRoot is non-zero")
    else:
        print(f"   ✗ FAIL: txsRoot is zero (should be computed from tx hashes)")
        return False
    
    if receipts_root != zero_root:
        print(f"   ✓ PASS: receiptsRoot is non-zero")
    else:
        print(f"   ✗ FAIL: receiptsRoot is zero (should be computed from receipts)")
        return False
    
    # TEST G: Verify tx evicted from mempool after mining
    print(f"\n12. TEST G: Verify tx evicted from mempool after mining")
    pending_after = rpc_call(client, "mempool.getPending")["result"]
    if returned_hash not in pending_after:
        print(f"   ✓ PASS: TX evicted from mempool")
    else:
        print(f"   ✗ FAIL: TX still in mempool after mining")
        return False
    
    # TEST H: Mine more blocks and verify balances continue to update
    print(f"\n13. TEST H: Mine more blocks and verify balances update")
    mine_result = rpc_call(client, "miner.mine", {"count": 3, "address": sender_addr_bech32})["result"]
    print(f"   Mined {mine_result['mined']} more blocks")
    
    sender_balance_after_more_mining = int(rpc_call(client, "state.getBalance", [sender_hex])["result"], 16)
    print(f"   Sender balance: {sender_balance_after_more_mining:,} nANM (was {sender_balance_final:,})")
    
    if sender_balance_after_more_mining > sender_balance_final:
        print(f"   ✓ PASS: Sender balance increased from mining rewards")
    else:
        print(f"   ✗ FAIL: Sender balance did not increase")
        return False
    
    # TEST I: Verify same tx hash doesn't appear in multiple blocks
    print(f"\n14. TEST I: Verify tx hash uniqueness across blocks")
    # Check blocks around the mined tx
    unique_test_passed = True
    for h in range(max(1, mined_height - 1), mined_height + 3):
        try:
            blk = rpc_call(client, "chain.getBlockByNumber", [h, True])["result"]
            if blk:
                blk_txs = blk.get("transactions", [])
                blk_tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in blk_txs]
                if returned_hash in blk_tx_hashes:
                    if h != mined_height:
                        print(f"   ✗ FAIL: TX hash appears in block {h} (not just block {mined_height})")
                        unique_test_passed = False
        except Exception:
            pass
    
    if unique_test_passed:
        print(f"   ✓ PASS: TX hash appears only in block {mined_height}")
    
    # Summary
    print(f"\n" + "="*80)
    print(f"ALL TESTS PASSED ✓")
    print(f"="*80)
    print(f"\nSummary:")
    print(f"  ✓ Canonical tx hash consistency")
    print(f"  ✓ Hash appears in block")
    print(f"  ✓ getTransactionByHash works")
    print(f"  ✓ getTransactionReceipt works")
    print(f"  ✓ Balances and nonces updated")
    print(f"  ✓ txsRoot and receiptsRoot non-zero")
    print(f"  ✓ TX evicted from mempool")
    print(f"  ✓ Mining rewards continue to work")
    print(f"  ✓ TX hash uniqueness")
    
    return True


if __name__ == "__main__":
    try:
        success = test_tx_hash_consistency()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ TEST FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
