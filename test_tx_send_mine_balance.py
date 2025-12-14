#!/usr/bin/env python3
"""
Minimal test to reproduce the tx send + mine + balance issue.

Scenario (from problem statement):
1. Send tx from A to B using `animica tx send`
2. Mine a block using `animica miner mine-blocks`
3. Check balance of B using `animica wallet show`
4. Expected: B's balance should increase
5. Actual: B's balance is still 0

This test simulates this flow via RPC calls.
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(__file__))

def test_tx_send_and_mine_updates_balance():
    """Test that sending a tx and mining a block updates balances."""
    print("\n" + "="*70)
    print("TEST: Transaction send + mine updates balances")
    print("="*70)
    
    # Import test helpers
    try:
        from rpc.tests import new_test_client, rpc_call
    except ImportError as e:
        print(f"SKIP: Cannot import test helpers: {e}")
        return True
    
    # Create test client
    client, cfg, _ = new_test_client()
    
    # Generate two addresses for sender and recipient
    try:
        from pq.py.keygen import keygen_sig
        from pq.py.address import decode_address
    except ImportError as e:
        print(f"SKIP: Cannot import PQ modules: {e}")
        return True
    
    try:
        # Generate sender and recipient keypairs
        sender_kp = keygen_sig("dilithium3")
        recipient_kp = keygen_sig("dilithium3")
    except Exception as e:
        print(f"SKIP: PQ keygen not available: {e}")
        return True
    
    sender_addr_bech32 = sender_kp.address
    recipient_addr_bech32 = recipient_kp.address
    
    # Decode to get 32-byte addresses for state queries
    sender_record = decode_address(sender_addr_bech32)
    recipient_record = decode_address(recipient_addr_bech32)
    
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    recipient_bytes = bytes(recipient_record.digest)[:32].ljust(32, b"\x00")
    
    sender_hex = "0x" + sender_bytes.hex()
    recipient_hex = "0x" + recipient_bytes.hex()
    
    print(f"\n1. Setup addresses")
    print(f"   Sender (bech32):    {sender_addr_bech32}")
    print(f"   Sender (hex):       {sender_hex}")
    print(f"   Recipient (bech32): {recipient_addr_bech32}")
    print(f"   Recipient (hex):    {recipient_hex}")
    
    # Fund sender by mining blocks to it
    print(f"\n2. Fund sender by mining 3 blocks")
    mine_result = rpc_call(client, "miner.mine", {"count": 3, "address": sender_addr_bech32})["result"]
    print(f"   Mined {mine_result['mined']} blocks, total reward: {mine_result['totalReward']} nANM")
    
    # Check sender balance
    sender_balance_initial = rpc_call(client, "state.getBalance", [sender_hex])["result"]
    if isinstance(sender_balance_initial, str):
        sender_balance_initial = int(sender_balance_initial, 16) if sender_balance_initial.startswith("0x") else int(sender_balance_initial)
    
    recipient_balance_initial = rpc_call(client, "state.getBalance", [recipient_hex])["result"]
    if isinstance(recipient_balance_initial, str):
        recipient_balance_initial = int(recipient_balance_initial, 16) if recipient_balance_initial.startswith("0x") else int(recipient_balance_initial)
    
    print(f"   Sender balance:    {sender_balance_initial:,} nANM")
    print(f"   Recipient balance: {recipient_balance_initial:,} nANM")
    
    # Build and sign a transaction
    print(f"\n3. Build and send transaction")
    
    from core.encoding.canonical import tx_sign_bytes
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    from pq.py import sign
    from pq.py.registry import ALG_ID
    
    transfer_amount = 1_000_000_000  # 1 ANM
    
    # Get sender nonce
    sender_nonce = rpc_call(client, "state.getNonce", [sender_hex])["result"]
    if isinstance(sender_nonce, str):
        sender_nonce = int(sender_nonce, 16) if sender_nonce.startswith("0x") else int(sender_nonce)
    
    print(f"   Sender nonce: {sender_nonce}")
    print(f"   Transfer amount: {transfer_amount:,} nANM (1 ANM)")
    
    # Build unsigned transfer
    unsigned = UnsignedTx(
        chain_id=cfg.chain_id,
        nonce=sender_nonce,
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
    tx_hash = "0x" + tx.txid().hex()
    
    print(f"   TX hash: {tx_hash}")
    
    # Submit transaction
    try:
        result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
        returned_hash = result.get("result")
        print(f"   ✓ Transaction submitted: {returned_hash}")
        
        if returned_hash != tx_hash:
            print(f"   ⚠ WARNING: Hash mismatch! Returned {returned_hash} != expected {tx_hash}")
    except Exception as e:
        print(f"   ✗ ERROR: Failed to submit transaction: {e}")
        return False
    
    # Check mempool
    print(f"\n4. Check mempool")
    pending = rpc_call(client, "mempool.getPending")["result"]
    print(f"   Pending count: {len(pending)}")
    
    if tx_hash in pending:
        print(f"   ✓ TX {tx_hash[:16]}... is in mempool")
    else:
        print(f"   ✗ TX {tx_hash[:16]}... NOT in mempool")
        print(f"   Pending hashes: {[h[:16] + '...' for h in pending[:5]]}")
    
    # Mine a block (should include the transaction)
    print(f"\n5. Mine block")
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_addr_bech32})["result"]
    print(f"   Mined {mine_result['mined']} block(s) at height {mine_result['height']}")
    
    # Check if tx was included in the block
    print(f"\n6. Check if transaction was included in block")
    block = rpc_call(client, "chain.getBlockByNumber", [mine_result["height"], True])["result"]
    block_txs = block.get("transactions", [])
    tx_hashes_in_block = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    
    print(f"   Block contains {len(block_txs)} transaction(s)")
    if tx_hash in tx_hashes_in_block:
        print(f"   ✓ TX {tx_hash[:16]}... WAS included in block")
    else:
        print(f"   ✗ TX {tx_hash[:16]}... NOT included in block")
        if tx_hashes_in_block:
            print(f"   Block tx hashes: {[h[:16] + '...' for h in tx_hashes_in_block[:5]]}")
    
    # Check balances after mining
    print(f"\n7. Check balances after mining")
    sender_balance_final = rpc_call(client, "state.getBalance", [sender_hex])["result"]
    if isinstance(sender_balance_final, str):
        sender_balance_final = int(sender_balance_final, 16) if sender_balance_final.startswith("0x") else int(sender_balance_final)
    
    recipient_balance_final = rpc_call(client, "state.getBalance", [recipient_hex])["result"]
    if isinstance(recipient_balance_final, str):
        recipient_balance_final = int(recipient_balance_final, 16) if recipient_balance_final.startswith("0x") else int(recipient_balance_final)
    
    print(f"   Sender balance:    {sender_balance_final:,} nANM (was {sender_balance_initial:,})")
    print(f"   Recipient balance: {recipient_balance_final:,} nANM (was {recipient_balance_initial:,})")
    
    # Verify balances
    print(f"\n8. Verify balance changes")
    
    # Calculate expected changes
    gas_fee = 21_000 * 1  # gas_limit * gas_price
    expected_sender_deduction = transfer_amount + gas_fee
    expected_recipient_credit = transfer_amount
    
    # Sender should be debited (transfer + fees), but also credited with mining reward
    # So we can't do exact math, but we can check recipient increased
    
    recipient_increase = recipient_balance_final - recipient_balance_initial
    
    if recipient_increase == expected_recipient_credit:
        print(f"   ✓ Recipient balance increased correctly by {recipient_increase:,} nANM")
        success = True
    elif recipient_increase == 0:
        print(f"   ✗ BUG: Recipient balance did NOT increase (still {recipient_balance_final:,})")
        success = False
    else:
        print(f"   ⚠ Recipient balance increased by {recipient_increase:,}, expected {expected_recipient_credit:,}")
        success = False
    
    # Summary
    print(f"\n" + "="*70)
    print(f"TEST RESULT: {'✓ PASS' if success else '✗ FAIL'}")
    print(f"="*70)
    
    return success


if __name__ == "__main__":
    success = test_tx_send_and_mine_updates_balance()
    sys.exit(0 if success else 1)
