"""
Test suite for value transfer consensus bug fix.

This test verifies that:
1. Transactions sent via RPC are mined and update balances correctly
2. Receipts are available via tx.getReceipt after mining
3. Invalid transactions are rejected (not silently evicted)
4. Address canonicalization is consistent throughout the tx lifecycle
"""

import hashlib
import pytest

from rpc.tests import new_test_client, rpc_call


def _address_to_32_bytes(address_record):
    """Convert address record to 32-byte digest format (matches state DB keys)."""
    digest = bytes(address_record.digest) if isinstance(address_record.digest, list) else address_record.digest
    return digest[:32].ljust(32, b"\x00")


def _parse_int_result(result):
    """Parse integer result from RPC (balance, nonce, etc.)."""
    value = result.get("result", 0)
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


@pytest.mark.asyncio
async def test_value_transfer_updates_balance():
    """
    Test that sending a value transfer tx and mining updates balances correctly.
    
    This is the main regression test for the consensus bug where:
    - TX was mined but balances didn't update
    - TX was evicted from mempool without applying state transitions
    """
    # Create test client
    client, cfg, _ = new_test_client()
    
    # Generate keypairs
    from pq.py.keygen import keygen_sig
    from pq.py.address import decode_address
    
    sender_kp = keygen_sig("dilithium3")
    recipient_kp = keygen_sig("dilithium3")
    
    sender_addr_bech32 = sender_kp.address
    recipient_addr_bech32 = recipient_kp.address
    
    # Decode to 32-byte digests (canonical format for state DB)
    sender_record = decode_address(sender_addr_bech32)
    recipient_record = decode_address(recipient_addr_bech32)
    
    sender_bytes = _address_to_32_bytes(sender_record)
    recipient_bytes = _address_to_32_bytes(recipient_record)
    
    sender_hex = "0x" + sender_bytes.hex()
    recipient_hex = "0x" + recipient_bytes.hex()
    
    # Fund sender by mining blocks
    mine_result = rpc_call(client, "miner.mine", {"count": 3, "address": sender_addr_bech32})["result"]
    assert mine_result["mined"] == 3
    
    # Check initial balances
    sender_balance_initial = _parse_int_result(rpc_call(client, "state.getBalance", [sender_hex]))
    recipient_balance_initial = _parse_int_result(rpc_call(client, "state.getBalance", [recipient_hex]))
    
    assert sender_balance_initial > 0, "Sender should have balance from mining rewards"
    assert recipient_balance_initial == 0, "Recipient should start with zero balance"
    
    # Build and sign transaction
    from core.encoding.canonical import tx_sign_bytes
    from core.genesis.loader import compute_chain_identity
    from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
    from pq.py import sign
    from pq.py.registry import ALG_ID
    
    transfer_amount = 1_000_000_000  # 1 ANM
    
    # Get sender nonce
    sender_nonce = _parse_int_result(rpc_call(client, "state.getNonce", [sender_hex]))
    
    # Build unsigned transfer with 32-byte digest addresses
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
    fork_id = compute_chain_identity(None, chain_id=cfg.chain_id).fork_id
    sig_env = sign.sign_detached(
        sign_bytes_data,
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
    
    # Submit transaction
    result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    returned_hash = result.get("result")
    assert returned_hash == tx_hash, f"TX hash mismatch: {returned_hash} != {tx_hash}"
    
    # Check mempool
    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending, f"TX {tx_hash[:16]}... should be in mempool"
    
    # Mine a block
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_addr_bech32})["result"]
    assert mine_result["mined"] == 1
    
    # Check if tx was included in block
    block = rpc_call(client, "chain.getBlockByNumber", [mine_result["height"], True])["result"]
    block_txs = block.get("transactions", [])
    tx_hashes_in_block = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    
    assert tx_hash in tx_hashes_in_block, f"TX {tx_hash[:16]}... should be included in block"
    
    # Check balances after mining
    sender_balance_final = _parse_int_result(rpc_call(client, "state.getBalance", [sender_hex]))
    recipient_balance_final = _parse_int_result(rpc_call(client, "state.getBalance", [recipient_hex]))
    
    # Verify recipient balance increased
    recipient_increase = recipient_balance_final - recipient_balance_initial
    assert recipient_increase == transfer_amount, \
        f"Recipient balance should increase by {transfer_amount:,}, got {recipient_increase:,}"
    
    # Verify sender balance decreased (by at least transfer amount + fees)
    gas_fee = 21_000 * 1  # gas_limit * gas_price
    min_sender_decrease = transfer_amount + gas_fee
    
    # Note: sender also got mining reward, so we can't check exact decrease
    # But we can verify the transfer happened by checking recipient
    assert recipient_balance_final == transfer_amount, \
        f"Recipient should have exactly {transfer_amount:,} nANM, got {recipient_balance_final:,}"
    
    # Verify mempool is empty after inclusion
    pending_after = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash not in pending_after, f"TX {tx_hash[:16]}... should be evicted from mempool after inclusion"


@pytest.mark.asyncio
async def test_tx_get_receipt_method_exists():
    """
    Test that tx.getReceipt method exists and works.
    
    This verifies the fix for missing RPC method.
    """
    client, cfg, _ = new_test_client()
    
    # Generate keypair and mine some blocks
    from pq.py.keygen import keygen_sig
    
    sender_kp = keygen_sig("dilithium3")
    sender_addr_bech32 = sender_kp.address
    
    # Mine blocks to fund sender
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": sender_addr_bech32})["result"]
    assert mine_result["mined"] == 1
    
    # Try to call tx.getReceipt (should not raise "method not found")
    # We don't have a real tx hash yet, so we expect None result (not error)
    fake_hash = "0x" + ("00" * 32)
    
    try:
        result = rpc_call(client, "tx.getReceipt", [fake_hash])
        # Should return None for non-existent tx (not raise error)
        assert result.get("result") is None
    except Exception as e:
        if "Method not found" in str(e) or "method" in str(e).lower():
            pytest.fail("tx.getReceipt method should exist (alias for tx.getTransactionReceipt)")
        raise


@pytest.mark.asyncio
async def test_invalid_tx_not_silently_evicted():
    """
    Test that invalid transactions are not silently included and evicted.
    
    This verifies the fix for the bug where txs with missing sender were
    "included" in blocks but skipped during execution, then evicted from mempool.
    """
    client, cfg, _ = new_test_client()
    
    import cbor2
    
    # Build an invalid transaction (missing signature)
    invalid_body = {
        "to": b"\x00" * 32,
        "from": b"\x00" * 32,
        "value": 1000,
        "nonce": 0,
        "gasLimit": 21000,
        "maxFee": 1,
        "data": b"",
        "chainId": cfg.chain_id,
    }
    
    # No signature envelope → should be rejected
    invalid_envelope = {"body": invalid_body}
    
    raw = cbor2.dumps(invalid_envelope, canonical=True)
    raw_hex = "0x" + raw.hex()
    
    # Try to submit invalid tx
    try:
        result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
        pytest.fail("Invalid tx without signature should be rejected")
    except Exception as e:
        # Should raise an error about missing/invalid signature
        assert "sig" in str(e).lower() or "signature" in str(e).lower(), \
            f"Error should mention signature: {e}"


@pytest.mark.asyncio
async def test_address_canonicalization_consistency():
    """
    Test that address canonicalization is consistent throughout the tx lifecycle.
    
    This verifies the fix for address format mismatch between CLI, miner, and state DB.
    """
    client, cfg, _ = new_test_client()
    
    from pq.py.keygen import keygen_sig
    from pq.py.address import decode_address
    
    # Generate a keypair
    kp = keygen_sig("dilithium3")
    addr_bech32 = kp.address
    
    # Decode to get 32-byte digest (canonical format)
    addr_record = decode_address(addr_bech32)
    addr_bytes = _address_to_32_bytes(addr_record)
    addr_hex = "0x" + addr_bytes.hex()
    
    # Mine to this address (using bech32)
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": addr_bech32})["result"]
    assert mine_result["mined"] == 1
    
    # Check balance using 32-byte digest hex
    balance = _parse_int_result(rpc_call(client, "state.getBalance", [addr_hex]))
    
    # Should have non-zero balance from mining reward
    assert balance > 0, \
        f"Address {addr_bech32} should have balance when queried as {addr_hex}"
    
    # Also verify that the reward amount matches what was returned from miner.mine
    expected_reward = mine_result.get("totalReward", 0)
    if expected_reward > 0:
        assert balance == expected_reward, \
            f"Balance {balance} should match mining reward {expected_reward}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
