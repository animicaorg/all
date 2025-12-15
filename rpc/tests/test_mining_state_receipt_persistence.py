"""
Regression test for mining state and receipt persistence issue.

Tests the complete end-to-end flow:
1. Mine blocks with block rewards → verify state persists and balances reflect it
2. Submit transaction → verify tx hash consistency (sendRawTransaction vs block inclusion)
3. Mine block containing tx → verify state updates, receipts persist, tx hash matches
4. Verify getTransactionReceipt returns persisted receipt using canonical tx hash

This test reproduces the bug described in the problem statement where:
- Mining advances chain.getHead height but balances/state do not persist
- Mempool empties but tx is not reflected in state
- tx.getTransactionReceipt stays null forever
- Blocks contain tx hash that differs from CLI/sendRawTransaction tx hash
"""

import pytest
from rpc.tests import new_test_client, rpc_call


def _parse_balance_result(result: dict) -> int:
    """Parse balance result from RPC (handles both hex and int formats)."""
    value = result.get("result", 0)
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def _parse_integer_result(result: dict) -> int:
    """Parse integer result from RPC (balance, nonce, etc.)."""
    return _parse_balance_result(result)


def _build_signed_transfer(client, cfg, sender_kp, recipient_hex: str, nonce: int = 0, value: int = 1_000_000_000):
    """Build a signed transfer transaction using provided keypair."""
    from core.encoding.canonical import tx_sign_bytes
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    from pq.py import sign
    from pq.py.address import decode_address
    from pq.py.registry import ALG_ID
    
    alg_name = "dilithium3"
    
    # Decode sender address
    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    
    # Recipient is hex, convert to bytes
    recipient_bytes = bytes.fromhex(recipient_hex[2:] if recipient_hex.startswith("0x") else recipient_hex)
    
    # Build unsigned transfer
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
    sig_env = sign.sign_detached(sign_bytes, alg_name, sender_kp.secret_key, domain="tx", chain_id=cfg.chain_id)
    
    # Create signed tx
    sig = PqSignature(alg_id=ALG_ID[alg_name], pubkey=sender_kp.public_key, sig=sig_env.sig)
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Encode to CBOR hex
    cbor_bytes = tx.to_cbor()
    raw_hex = "0x" + cbor_bytes.hex()
    # Compute canonical tx hash: sha3_256(raw_cbor_bytes)
    import hashlib
    tx_hash_bytes = hashlib.sha3_256(cbor_bytes).digest()
    tx_hash = "0x" + tx_hash_bytes.hex()
    
    return raw_hex, tx_hash


def test_mining_block_reward_persists():
    """
    Test that mining blocks with block rewards updates state and persists balances.
    
    Verifies:
    - Mine N blocks to address P1
    - state.getBalance(P1) == N * reward (approximately, accounting for emission schedule)
    - Each mined block increases balance consistently
    """
    client, cfg, _ = new_test_client()
    
    # Generate keypair for P1
    from pq.py.keygen import keygen_sig
    
    try:
        p1_kp = keygen_sig("dilithium3")
    except Exception:
        pytest.skip("PQ keygen not available")
        return
    
    # Get P1 address
    from pq.py.address import decode_address
    
    p1_record = decode_address(p1_kp.address)
    p1_bytes = bytes(p1_record.digest)[:32].ljust(32, b"\x00")
    p1_hex = "0x" + p1_bytes.hex()
    
    print(f"P1 address: {p1_hex}")
    
    # Mine 5 blocks to P1 and verify balance increases after each block
    initial_balance = _parse_integer_result(rpc_call(client, "state.getBalance", [p1_hex]))
    print(f"Initial P1 balance: {initial_balance} nANM")
    assert initial_balance == 0, "P1 should start with zero balance"
    
    # Mine blocks one at a time and verify balance increases
    for i in range(1, 6):
        print(f"\n--- Mining block {i} ---")
        mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": p1_kp.address})["result"]
        assert mine_result["mined"] == 1, f"Should mine 1 block (iteration {i})"
        
        # Check that balance increased
        balance_after = _parse_integer_result(rpc_call(client, "state.getBalance", [p1_hex]))
        print(f"P1 balance after block {i}: {balance_after} nANM")
        
        # Balance should increase (we can't know exact amount due to emission schedule)
        # but it must be > 0 after first block and increase monotonically
        if i == 1:
            assert balance_after > 0, f"P1 balance should be > 0 after mining first block, got {balance_after}"
        else:
            assert balance_after > initial_balance, \
                f"P1 balance should increase after block {i}: {initial_balance} -> {balance_after}"
        
        initial_balance = balance_after
    
    # Verify final balance is reasonable (at least 5 blocks worth of some reward)
    # Assuming minimum reward per block is at least 1 nANM
    assert initial_balance >= 5, f"P1 should have at least 5 nANM after 5 blocks, got {initial_balance}"
    
    print(f"\n✓ Final P1 balance: {initial_balance} nANM (≥ 5 blocks of rewards)")


def test_tx_hash_consistency_and_receipt_persistence():
    """
    Test that transaction hashes are consistent and receipts persist.
    
    Verifies:
    - tx.sendRawTransaction returns canonical hash H
    - After mining, chain.getBlockByHeight contains EXACT hash H
    - tx.getTransactionReceipt(H) returns non-null receipt
    - Receipt includes correct blockNumber, blockHash, status
    - Balances are updated correctly after mining
    """
    client, cfg, _ = new_test_client()
    
    # Generate keypairs for sender and receiver
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
    
    # Fund sender by mining blocks
    print("\n--- Funding sender ---")
    mine_result = rpc_call(client, "miner.mine", {"count": 3, "address": sender_kp.address})["result"]
    assert mine_result["mined"] == 3, "Should mine 3 blocks for funding"
    
    # Verify sender has funds
    sender_balance_initial = _parse_integer_result(rpc_call(client, "state.getBalance", [sender_hex]))
    print(f"Sender balance after funding: {sender_balance_initial} nANM")
    assert sender_balance_initial > 0, f"Sender should have funds from mining, got {sender_balance_initial}"
    
    # Verify receiver has no funds
    receiver_balance_initial = _parse_integer_result(rpc_call(client, "state.getBalance", [receiver_hex]))
    print(f"Receiver balance initially: {receiver_balance_initial} nANM")
    assert receiver_balance_initial == 0, "Receiver should have zero balance initially"
    
    # Send transaction from sender to receiver
    print("\n--- Sending transaction ---")
    transfer_amount = 1_000_000_000  # 1 ANM = 1e9 nANM (base units)
    raw_hex, tx_hash_from_send = _build_signed_transfer(
        client, cfg, sender_kp, receiver_hex, nonce=0, value=transfer_amount
    )
    
    # Submit transaction and capture returned hash
    result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert "result" in result, f"Expected result, got {result}"
    tx_hash_returned = result["result"]
    
    print(f"TX hash from sendRawTransaction: {tx_hash_returned}")
    print(f"TX hash computed locally:        {tx_hash_from_send}")
    
    # Verify hashes match
    assert tx_hash_returned == tx_hash_from_send, \
        f"Hash from sendRawTransaction should match computed hash: {tx_hash_returned} != {tx_hash_from_send}"
    
    # Store canonical hash for subsequent checks
    tx_hash_canonical = tx_hash_returned
    
    # Verify transaction is in mempool
    print("\n--- Verifying tx in mempool ---")
    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash_canonical in pending, \
        f"TX {tx_hash_canonical} should be in mempool, got {list(pending.keys())}"
    print(f"✓ Transaction in mempool (count: {len(pending)})")
    
    # Verify receipt is null before mining (tx is still pending)
    receipt_before = rpc_call(client, "tx.getTransactionReceipt", [tx_hash_canonical])["result"]
    assert receipt_before is None, "Receipt should be null for pending transaction"
    print("✓ Receipt is null before mining (tx pending)")
    
    # Mine a block
    print("\n--- Mining block ---")
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})["result"]
    assert mine_result["mined"] == 1, "Should mine exactly 1 block"
    block_height = mine_result["height"]
    print(f"Mined block at height: {block_height}")
    
    # Get the mined block
    block = rpc_call(client, "chain.getBlockByNumber", [block_height, True])["result"]
    assert block is not None, f"Block at height {block_height} should exist"
    block_hash = block.get("hash")
    print(f"Block hash: {block_hash}")
    
    # Verify transaction is in the block with EXACT hash match
    print("\n--- Verifying tx in block ---")
    block_txs = block.get("transactions", [])
    tx_hashes_in_block = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    
    print(f"Block contains {len(block_txs)} transactions")
    print(f"Transaction hashes in block: {tx_hashes_in_block}")
    print(f"Looking for canonical hash: {tx_hash_canonical}")
    
    assert tx_hash_canonical in tx_hashes_in_block, \
        f"TX {tx_hash_canonical} should be in block txs: {tx_hashes_in_block}"
    print(f"✓ Transaction {tx_hash_canonical} included in block {block_height}")
    
    # Verify balances updated
    print("\n--- Verifying balances updated ---")
    receiver_balance_final = _parse_integer_result(rpc_call(client, "state.getBalance", [receiver_hex]))
    sender_balance_final = _parse_integer_result(rpc_call(client, "state.getBalance", [sender_hex]))
    
    print(f"Receiver balance after mining: {receiver_balance_final} nANM")
    print(f"Sender balance after mining: {sender_balance_final} nANM")
    
    # Receiver should have received the transfer amount
    assert receiver_balance_final == transfer_amount, \
        f"Receiver should have {transfer_amount} nANM, got {receiver_balance_final}"
    print(f"✓ Receiver received {transfer_amount} nANM")
    
    # Sender balance should reflect: initial + mining_reward - transfer - fees
    # We can't compute exact expected value due to unknown fees, but balance should be reasonable
    mining_reward = mine_result.get("totalReward", 0)
    expected_min = sender_balance_initial + mining_reward - transfer_amount - 100_000  # Allow for fees
    assert sender_balance_final >= expected_min, \
        f"Sender balance should be at least {expected_min}, got {sender_balance_final}"
    print(f"✓ Sender balance adjusted correctly (reward: {mining_reward} nANM)")
    
    # Verify receipt is now available with canonical hash
    print("\n--- Verifying receipt persisted ---")
    receipt = rpc_call(client, "tx.getTransactionReceipt", [tx_hash_canonical])["result"]
    
    # Receipt should be non-null
    assert receipt is not None, \
        f"Receipt for {tx_hash_canonical} should be non-null after mining, got None"
    
    # Verify receipt fields
    assert receipt.get("transactionHash") == tx_hash_canonical, \
        f"Receipt txHash should match: {receipt.get('transactionHash')} != {tx_hash_canonical}"
    assert receipt.get("blockNumber") == block_height, \
        f"Receipt blockNumber should be {block_height}, got {receipt.get('blockNumber')}"
    assert receipt.get("blockHash") == block_hash, \
        f"Receipt blockHash should match: {receipt.get('blockHash')} != {block_hash}"
    # Status can be integer (1 for success) or string ("SUCCESS")
    status = receipt.get("status")
    assert status in (1, "SUCCESS", "ReceiptStatus.SUCCESS"), \
        f"Receipt status should indicate success, got {status}"
    
    print(f"✓ Receipt persisted with correct fields:")
    print(f"  - transactionHash: {receipt.get('transactionHash')}")
    print(f"  - blockNumber: {receipt.get('blockNumber')}")
    print(f"  - blockHash: {receipt.get('blockHash')}")
    print(f"  - status: {receipt.get('status')}")
    print(f"  - gasUsed: {receipt.get('gasUsed')}")
    
    # Verify transaction is removed from mempool
    print("\n--- Verifying tx removed from mempool ---")
    pending_after = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash_canonical not in pending_after, \
        f"TX {tx_hash_canonical} should not be in mempool after mining"
    print(f"✓ Transaction removed from mempool (remaining: {len(pending_after)})")
    
    print("\n✅ All checks passed!")


def test_multiple_blocks_with_rewards():
    """
    Test that mining multiple blocks in sequence updates balances correctly.
    
    Verifies:
    - Mine 10 blocks to P1
    - Balance increases consistently across all blocks
    - Final balance reflects all block rewards
    """
    client, cfg, _ = new_test_client()
    
    # Generate keypair
    from pq.py.keygen import keygen_sig
    
    try:
        p1_kp = keygen_sig("dilithium3")
    except Exception:
        pytest.skip("PQ keygen not available")
        return
    
    # Get address
    from pq.py.address import decode_address
    
    p1_record = decode_address(p1_kp.address)
    p1_bytes = bytes(p1_record.digest)[:32].ljust(32, b"\x00")
    p1_hex = "0x" + p1_bytes.hex()
    
    print(f"P1 address: {p1_hex}")
    
    # Mine 10 blocks and track balance
    initial_balance = _parse_integer_result(rpc_call(client, "state.getBalance", [p1_hex]))
    print(f"Initial balance: {initial_balance} nANM")
    
    print("\n--- Mining 10 blocks ---")
    mine_result = rpc_call(client, "miner.mine", {"count": 10, "address": p1_kp.address})["result"]
    assert mine_result["mined"] == 10, "Should mine 10 blocks"
    
    final_balance = _parse_integer_result(rpc_call(client, "state.getBalance", [p1_hex]))
    print(f"Final balance after 10 blocks: {final_balance} nANM")
    
    # Balance should have increased significantly (at least 10 nANM minimum)
    assert final_balance > initial_balance, \
        f"Balance should increase after mining: {initial_balance} -> {final_balance}"
    assert final_balance >= 10, \
        f"Balance should be at least 10 nANM after 10 blocks, got {final_balance}"
    
    total_reward = mine_result.get("totalReward", 0)
    print(f"Total rewards from mining: {total_reward} nANM")
    
    # Balance should match rewards (approximately, accounting for any prior state)
    assert final_balance >= total_reward, \
        f"Final balance ({final_balance}) should be at least total reward ({total_reward})"
    
    print(f"\n✓ Balance increased correctly: {initial_balance} -> {final_balance} nANM")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
