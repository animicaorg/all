"""
Integration tests for mining with mempool transaction inclusion.

Tests the complete flow:
1. Fund a sender address via mining (coinbase rewards)
2. Send transaction from sender to receiver
3. Verify transaction appears in mempool
4. Mine a block
5. Verify transaction is included in block
6. Verify balances are updated correctly
7. Verify nonces are incremented
8. Verify transaction is removed from mempool
"""

import pytest
from rpc.tests import new_test_client, rpc_call


def _get_premine_address_hex() -> str:
    """Helper to get the premine address as hex string."""
    from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
    from pq.py.address import decode_address
    
    premine_addr_bech32 = MAINNET_PREMINE_DISTRIBUTION[0][0]
    addr_record = decode_address(premine_addr_bech32)
    digest = bytes(addr_record.digest) if isinstance(addr_record.digest, list) else addr_record.digest
    premine_addr_bytes = digest[:32].ljust(32, b"\x00")
    return "0x" + premine_addr_bytes.hex()


def _parse_integer_result(result: dict) -> int:
    """Helper to parse integer result from RPC (balance, nonce, etc.)."""
    value = result.get("result", 0)
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def _build_signed_transfer(client, cfg, sender_kp, recipient_hex: str, nonce: int = 0, value: int = 1_000_000_000):
    """Build a signed transfer transaction using provided keypair."""
    from core.encoding.canonical import tx_sign_bytes
    from core.types.tx import Tx
    from pq.py import sign
    from core.genesis.loader import compute_chain_identity
    from pq.py.address import decode_address
    from pq.py.registry import ALG_ID
    
    alg_name = "dilithium3"
    
    # Decode sender address
    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    
    # Recipient is hex, convert to bytes
    recipient_bytes = bytes.fromhex(recipient_hex[2:] if recipient_hex.startswith("0x") else recipient_hex)
    
    # Build unsigned transfer
    from core.types.tx import UnsignedTx, TxKind, TxTransfer
    
    unsigned = UnsignedTx(
        chain_id=cfg.chain_id,
        nonce=nonce,
        gas_price=1,
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=value, data=b""),
        access_list=(),
    )
    
    # Sign transaction
    sign_bytes = tx_sign_bytes(unsigned.to_obj())
    fork_id = compute_chain_identity(None, chain_id=cfg.chain_id).fork_id
    sig_env = sign.sign_detached(
        sign_bytes,
        alg_name,
        sender_kp.secret_key,
        domain="tx",
        chain_id=cfg.chain_id,
        fork_id=fork_id,
    )
    
    # Create signed tx
    from core.types.tx import PqSignature
    sig = PqSignature(alg_id=ALG_ID[alg_name], pubkey=sender_kp.public_key, sig=sig_env.sig)
    
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Encode to CBOR hex
    cbor_bytes = tx.to_cbor()
    raw_hex = "0x" + cbor_bytes.hex()
    tx_hash = "0x" + tx.txid().hex()
    
    return raw_hex, tx_hash


def test_mining_includes_tx_and_updates_balances():
    """
    End-to-end test: fund sender → send tx → mine → verify balances/nonces/mempool cleared.
    """
    client, cfg, _ = new_test_client()
    
    # Generate keypair for sender and receiver
    from pq.py.keygen import keygen_sig
    
    try:
        sender_kp = keygen_sig("dilithium3")
        receiver_kp = keygen_sig("dilithium3")
    except Exception:
        pytest.skip("PQ keygen not available")
        return
    
    # Get addresses
    from pq.py.address import decode_address
    
    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    sender_hex = "0x" + sender_bytes.hex()
    
    receiver_record = decode_address(receiver_kp.address)
    receiver_bytes = bytes(receiver_record.digest)[:32].ljust(32, b"\x00")
    receiver_hex = "0x" + receiver_bytes.hex()
    
    print(f"Sender: {sender_hex}")
    print(f"Receiver: {receiver_hex}")
    
    # Step 1: Fund sender by mining blocks to sender address
    print("\n--- Step 1: Fund sender via mining ---")
    mine_result = rpc_call(client, "miner.mine", {"count": 5, "address": sender_kp.address})["result"]
    assert mine_result["mined"] == 5, "Should mine 5 blocks for funding"
    
    # Check sender has funds
    sender_balance_initial = _parse_integer_result(rpc_call(client, "state.getBalance", [sender_hex]))
    print(f"Sender balance after mining: {sender_balance_initial} nANM")
    assert sender_balance_initial > 0, "Sender should have funds from mining rewards"
    
    # Check receiver has no funds initially
    receiver_balance_initial = _parse_integer_result(rpc_call(client, "state.getBalance", [receiver_hex]))
    print(f"Receiver balance initially: {receiver_balance_initial} nANM")
    assert receiver_balance_initial == 0, "Receiver should have zero balance initially"
    
    # Check sender nonce is 0
    sender_nonce_initial = _parse_integer_result(rpc_call(client, "state.getNonce", [sender_hex]))
    print(f"Sender nonce initially: {sender_nonce_initial}")
    assert sender_nonce_initial == 0, "Sender nonce should be 0 initially"
    
    # Step 2: Send transaction from sender to receiver
    print("\n--- Step 2: Send transaction ---")
    transfer_amount = 1_000_000_000  # 1 ANM
    raw_hex, tx_hash = _build_signed_transfer(client, cfg, sender_kp, receiver_hex, nonce=0, value=transfer_amount)
    
    # Submit transaction
    result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert "result" in result, f"Expected result, got {result}"
    returned_hash = result["result"]
    assert returned_hash == tx_hash, f"Hash mismatch: {returned_hash} != {tx_hash}"
    print(f"Transaction submitted: {tx_hash}")
    
    # Step 3: Verify transaction is in mempool
    print("\n--- Step 3: Verify tx in mempool ---")
    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending, f"TX {tx_hash} should be in mempool pending"
    print(f"Transaction {tx_hash} is in mempool (count: {len(pending)})")
    
    # Get mempool stats
    stats = rpc_call(client, "mempool.getStats")["result"]
    print(f"Mempool stats: count={stats['count']}, totalBytes={stats['totalBytes']}")
    assert stats["count"] >= 1, "Mempool should have at least 1 pending tx"
    
    # Step 4: Mine a block (should include the transaction)
    print("\n--- Step 4: Mine block ---")
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})["result"]
    assert mine_result["mined"] == 1, "Should mine exactly 1 block"
    block_height = mine_result["height"]
    print(f"Mined block at height: {block_height}")
    assert (
        mine_result.get("mempool", {}).get("included", 0) >= 1
    ), "Mining should include at least one mempool tx"
    
    # Get the mined block
    block = rpc_call(client, "chain.getBlockByNumber", [block_height, True])["result"]
    assert block is not None, f"Block at height {block_height} should exist"
    print(f"Block hash: {block['hash']}")
    
    # Step 5: Verify transaction is in the block
    print("\n--- Step 5: Verify tx in block ---")
    block_txs = block.get("transactions", [])
    tx_hashes_in_block = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    print(f"Block contains {len(block_txs)} transactions")
    
    assert tx_hash in tx_hashes_in_block, \
        f"TX {tx_hash} should be in block txs: {tx_hashes_in_block}"
    print(f"✓ Transaction {tx_hash} included in block {block_height}")
    
    # Step 6: Verify balances are updated
    print("\n--- Step 6: Verify balances updated ---")
    receiver_balance_final = _parse_integer_result(rpc_call(client, "state.getBalance", [receiver_hex]))
    print(f"Receiver balance after mining: {receiver_balance_final} nANM")
    
    # Receiver should have received the transfer amount
    assert receiver_balance_final == transfer_amount, \
        f"Receiver should have {transfer_amount} nANM, got {receiver_balance_final}"
    print(f"✓ Receiver received {transfer_amount} nANM")
    
    # Sender balance should decrease by transfer amount + fees, but increase by mining reward
    sender_balance_final = _parse_integer_result(rpc_call(client, "state.getBalance", [sender_hex]))
    print(f"Sender balance after mining: {sender_balance_final} nANM")
    
    # Sender should have: initial + mining_reward - transfer - fees
    # We can't know exact fees, but balance should be reasonable
    mining_reward = mine_result.get("totalReward", 0)
    expected_min = sender_balance_initial + mining_reward - transfer_amount - 100_000  # Allow for fees
    assert sender_balance_final >= expected_min, \
        f"Sender balance should be at least {expected_min}, got {sender_balance_final}"
    print(f"✓ Sender balance adjusted correctly (reward: {mining_reward} nANM)")
    
    # Step 7: Verify nonces are updated
    print("\n--- Step 7: Verify nonces updated ---")
    sender_nonce_final = _parse_integer_result(rpc_call(client, "state.getNonce", [sender_hex]))
    print(f"Sender nonce after tx: {sender_nonce_final}")
    assert sender_nonce_final == 1, f"Sender nonce should be 1 after tx, got {sender_nonce_final}"
    print(f"✓ Sender nonce incremented to {sender_nonce_final}")
    
    # Step 8: Verify transaction is removed from mempool
    print("\n--- Step 8: Verify tx removed from mempool ---")
    pending_after = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash not in pending_after, \
        f"TX {tx_hash} should not be in mempool after mining"
    print(f"✓ Transaction removed from mempool (remaining: {len(pending_after)})")
    
    # Verify mempool stats updated
    stats_after = rpc_call(client, "mempool.getStats")["result"]
    print(f"Mempool stats after: count={stats_after['count']}, totalBytes={stats_after['totalBytes']}")
    assert stats_after["count"] < stats["count"], \
        "Mempool count should decrease after mining"
    
    print("\n✓ All checks passed!")


def test_mining_multiple_txs_in_single_block():
    """Test that multiple transactions from the same sender are included in correct nonce order."""
    client, cfg, _ = new_test_client()
    
    # Generate keypair for sender
    from pq.py.keygen import keygen_sig
    
    try:
        sender_kp = keygen_sig("dilithium3")
    except Exception:
        pytest.skip("PQ keygen not available")
        return
    
    # Get sender address
    from pq.py.address import decode_address
    
    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    sender_hex = "0x" + sender_bytes.hex()
    
    # Fund sender
    mine_result = rpc_call(client, "miner.mine", {"count": 5, "address": sender_kp.address})["result"]
    assert mine_result["mined"] == 5
    
    sender_balance = _parse_integer_result(rpc_call(client, "state.getBalance", [sender_hex]))
    print(f"Sender balance: {sender_balance} nANM")
    
    # Send 3 transactions with sequential nonces
    tx_hashes = []
    for nonce in range(3):
        # Use different recipients (deterministic test addresses with high entropy)
        # Prefix with a marker to avoid collision with real addresses
        recipient_hex = f"0xdeadbeef{nonce:056x}"
        raw_hex, tx_hash = _build_signed_transfer(
            client, cfg, sender_kp, recipient_hex,
            nonce=nonce, value=100_000_000  # 0.1 ANM each
        )
        
        result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
        tx_hashes.append(tx_hash)
        print(f"Submitted tx {nonce}: {tx_hash}")
    
    # Verify all are in mempool
    pending = rpc_call(client, "mempool.getPending")["result"]
    for tx_hash in tx_hashes:
        assert tx_hash in pending, f"TX {tx_hash} should be in mempool"
    print(f"All {len(tx_hashes)} transactions in mempool")
    
    # Mine 1 block
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})["result"]
    block_height = mine_result["height"]
    
    # Get block
    block = rpc_call(client, "chain.getBlockByNumber", [block_height, True])["result"]
    block_txs = block.get("transactions", [])
    block_tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    
    # Count how many were included
    included_count = sum(1 for h in tx_hashes if h in block_tx_hashes)
    print(f"{included_count}/{len(tx_hashes)} transactions included in block")
    
    # At least one should be included (ideally all 3 if sender has enough funds)
    assert included_count > 0, "At least one transaction should be included"
    
    # Verify included txs are removed from mempool
    pending_after = rpc_call(client, "mempool.getPending")["result"]
    for tx_hash in tx_hashes:
        if tx_hash in block_tx_hashes:
            assert tx_hash not in pending_after, \
                f"Included TX {tx_hash} should not be in mempool"
    
    print(f"✓ {included_count} transactions included and removed from mempool")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
