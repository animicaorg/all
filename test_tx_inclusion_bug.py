"""
Minimal test to reproduce the "txs never confirm" bug.

Expected behavior:
1. Submit tx via RPC → appears in mempool
2. Mine block → tx included in block, balances updated, tx removed from mempool

Current behavior (bug):
1. Submit tx via RPC → appears in mempool  ✓
2. Mine block → tx NOT included, balances unchanged, tx stuck in mempool  ✗
"""

def test_tx_submission_and_mining():
    """
    Reproduce the bug where transactions are submitted but never included in mined blocks.
    """
    print("\n" + "="*70)
    print("TEST: Transaction submission and mining integration")
    print("="*70)
    
    # Import RPC test client
    from rpc.tests import new_test_client, rpc_call
    
    client, cfg, _ = new_test_client()
    
    # Generate keypair for sender
    from pq.py.keygen import keygen_sig
    from pq.py.address import decode_address
    
    try:
        sender_kp = keygen_sig("dilithium3")
    except Exception as e:
        print(f"SKIP: PQ keygen not available: {e}")
        return
    
    # Get sender address
    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    sender_hex = "0x" + sender_bytes.hex()
    
    print(f"\n1. Funding sender via mining")
    print(f"   Sender address: {sender_kp.address}")
    print(f"   Sender hex: {sender_hex}")
    
    # Fund sender by mining
    mine_result = rpc_call(client, "miner.mine", {"count": 3, "address": sender_kp.address})["result"]
    print(f"   Mined {mine_result['mined']} blocks")
    
    # Check sender balance
    sender_balance_initial = rpc_call(client, "state.getBalance", [sender_hex])["result"]
    if isinstance(sender_balance_initial, str):
        sender_balance_initial = int(sender_balance_initial, 16) if sender_balance_initial.startswith("0x") else int(sender_balance_initial)
    print(f"   Sender balance: {sender_balance_initial:,} nANM")
    
    # Build and sign transaction
    print(f"\n2. Building transaction")
    
    from core.encoding.canonical import tx_sign_bytes
    from core.genesis.loader import compute_chain_identity
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    from pq.py import sign
    from pq.py.registry import ALG_ID
    
    # Recipient address (deterministic test address)
    recipient_hex = "0xdead" + "00" * 30
    recipient_bytes = bytes.fromhex(recipient_hex[2:])
    
    # Build unsigned transfer
    transfer_amount = 1_000_000_000  # 1 ANM
    unsigned = UnsignedTx(
        chain_id=cfg.chain_id,
        nonce=0,
        gas_price=1,
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=transfer_amount, data=b""),
        access_list=(),
    )
    
    # Sign transaction
    sign_bytes = tx_sign_bytes(unsigned.to_obj())
    fork_id = compute_chain_identity(None, chain_id=cfg.chain_id).fork_id
    sig_env = sign.sign_detached(
        sign_bytes,
        "dilithium3",
        sender_kp.secret_key,
        domain="tx",
        chain_id=cfg.chain_id,
        fork_id=fork_id,
    )
    sig = PqSignature(alg_id=ALG_ID["dilithium3"], pubkey=sender_kp.public_key, sig=sig_env.sig)
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Encode to CBOR hex
    cbor_bytes = tx.to_cbor()
    raw_hex = "0x" + cbor_bytes.hex()
    tx_hash = "0x" + tx.txid().hex()
    
    print(f"   Transfer: {sender_hex[:10]}... → {recipient_hex[:10]}...")
    print(f"   Amount: {transfer_amount:,} nANM")
    print(f"   TX hash: {tx_hash}")
    
    # Submit transaction
    print(f"\n3. Submitting transaction to mempool")
    result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    returned_hash = result.get("result")
    print(f"   RPC returned: {returned_hash}")
    assert returned_hash == tx_hash, f"Hash mismatch: {returned_hash} != {tx_hash}"
    
    # Check mempool
    print(f"\n4. Checking mempool")
    pending = rpc_call(client, "mempool.getPending")["result"]
    print(f"   Pending count: {len(pending)}")
    print(f"   Pending hashes: {pending[:3]}{'...' if len(pending) > 3 else ''}")
    
    if tx_hash in pending:
        print(f"   ✓ TX {tx_hash} is in mempool")
    else:
        print(f"   ✗ TX {tx_hash} NOT in mempool (BUG!)")
        return False
    
    # Mine a block
    print(f"\n5. Mining block")
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})["result"]
    print(f"   Mined {mine_result['mined']} block(s) at height {mine_result['height']}")
    
    # Get block
    print(f"\n6. Checking if TX was included in block")
    block = rpc_call(client, "chain.getBlockByNumber", [mine_result["height"], True])["result"]
    block_txs = block.get("transactions", [])
    tx_hashes_in_block = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    print(f"   Block contains {len(block_txs)} transaction(s)")
    if tx_hashes_in_block:
        print(f"   Block tx hashes: {tx_hashes_in_block[:3]}")
    
    if tx_hash in tx_hashes_in_block:
        print(f"   ✓ TX {tx_hash} WAS included in block")
        tx_included = True
    else:
        print(f"   ✗ TX {tx_hash} NOT included in block (BUG!)")
        tx_included = False
    
    # Check if TX was removed from mempool
    print(f"\n7. Checking if TX was removed from mempool")
    pending_after = rpc_call(client, "mempool.getPending")["result"]
    print(f"   Pending count after mining: {len(pending_after)}")
    
    if tx_hash in pending_after:
        print(f"   ✗ TX {tx_hash} still in mempool (BUG!)")
        tx_removed = False
    else:
        print(f"   ✓ TX {tx_hash} removed from mempool")
        tx_removed = True
    
    # Check balances
    print(f"\n8. Checking balances")
    recipient_balance = rpc_call(client, "state.getBalance", [recipient_hex])["result"]
    if isinstance(recipient_balance, str):
        recipient_balance = int(recipient_balance, 16) if recipient_balance.startswith("0x") else int(recipient_balance)
    print(f"   Recipient balance: {recipient_balance:,} nANM")
    
    if recipient_balance == transfer_amount:
        print(f"   ✓ Recipient received {transfer_amount:,} nANM")
        balance_updated = True
    else:
        print(f"   ✗ Recipient balance is {recipient_balance:,}, expected {transfer_amount:,} (BUG!)")
        balance_updated = False
    
    # Summary
    print(f"\n" + "="*70)
    print(f"TEST RESULTS:")
    print(f"  TX submitted: ✓")
    print(f"  TX in mempool: ✓")
    print(f"  TX included in block: {'✓' if tx_included else '✗ BUG'}")
    print(f"  TX removed from mempool: {'✓' if tx_removed else '✗ BUG'}")
    print(f"  Balance updated: {'✓' if balance_updated else '✗ BUG'}")
    print(f"="*70)
    
    if tx_included and tx_removed and balance_updated:
        print("\n✓ ALL TESTS PASSED")
        return True
    else:
        print("\n✗ TEST FAILED - Transaction inclusion bug reproduced")
        return False


if __name__ == "__main__":
    import sys
    success = test_tx_submission_and_mining()
    sys.exit(0 if success else 1)
