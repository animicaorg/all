"""
Test transaction inclusion using CLI simplified envelope format.

This test verifies that transactions submitted in the CLI/SDK simplified
envelope format {body: {chainId, from, to, value, ...}, sig: {...}} are
correctly normalized to canonical core format and included in blocks.

This is the most direct test of the envelope normalization fix.
"""

import pytest
from rpc.tests import new_test_client, rpc_call


def _build_cli_format_tx(chain_id: int = 1):
    """
    Build a transaction in CLI simplified envelope format.
    
    This mimics what animica CLI `tx send` produces:
    {body: {chainId, from, to, value, gasLimit, maxFee, data}, sig: {algId, pk, sig}}
    """
    try:
        from pq.py import keygen, sign
        from pq.py.address import decode_address
        from pq.py.registry import ALG_ID
        from animica.tx.signing import build_signable_tx_bytes
        import cbor2
    except ImportError:
        pytest.skip("PQ or animica.tx.signing not available")
        return None, None
    
    # Generate keypair
    alg_name = "dilithium3"
    try:
        kp = keygen.keygen(alg_name)
    except Exception:
        pytest.skip("PQ keygen not available")
        return None, None
    
    # Build simplified tx body (CLI format)
    body = {
        "chainId": chain_id,
        "from": kp.address,  # bech32 string
        "to": kp.address,    # bech32 string (self-transfer)
        "value": 1_000_000_000,  # 1 ANM
        "nonce": 0,
        "gasLimit": 21000,
        "maxFee": 1000000000,  # 1 Gwei
        "data": b"",
    }
    
    # Sign body (using animica.tx.signing which handles CLI format)
    try:
        sign_bytes = build_signable_tx_bytes(body, chain_id=chain_id)
    except Exception:
        # Fallback to CBOR if build_signable_tx_bytes not available
        sign_bytes = cbor2.dumps(body, canonical=True)
    
    sig_env = sign.sign_detached(sign_bytes, alg_name, kp.secret_key, domain="tx")
    
    # Build CLI envelope
    envelope = {
        "body": body,
        "sig": {
            "algId": ALG_ID[alg_name],
            "pk": kp.public_key,
            "sig": sig_env.sig,
        },
    }
    
    # Encode to CBOR
    cbor_bytes = cbor2.dumps(envelope, canonical=True)
    raw_hex = "0x" + cbor_bytes.hex()
    
    # Compute tx hash (sha3_256 of full envelope)
    import hashlib
    tx_hash = "0x" + hashlib.sha3_256(cbor_bytes).hexdigest()
    
    return raw_hex, tx_hash


def test_cli_envelope_tx_appears_in_mempool():
    """Test that CLI format transaction appears in mempool.getPending."""
    client, cfg, _ = new_test_client()
    
    raw_hex, tx_hash = _build_cli_format_tx(chain_id=cfg.chain_id)
    if raw_hex is None:
        return  # Skipped
    
    # Submit transaction
    result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert "result" in result, f"Expected result, got {result}"
    returned_hash = result["result"]
    assert returned_hash == tx_hash, f"Hash mismatch: {returned_hash} != {tx_hash}"
    
    # Check mempool.getPending
    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending, f"CLI format TX {tx_hash} not in pending pool"
    
    print(f"✓ CLI format transaction {tx_hash} appears in mempool pending")


def test_cli_envelope_getTransactionByHash_returns_full_fields():
    """Test that tx.getTransactionByHash returns full fields for CLI format pending tx."""
    client, cfg, _ = new_test_client()
    
    raw_hex, tx_hash = _build_cli_format_tx(chain_id=cfg.chain_id)
    if raw_hex is None:
        return
    
    # Submit transaction
    rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    
    # Get transaction by hash
    tx_view = rpc_call(client, "tx.getTransactionByHash", {"txHash": tx_hash})["result"]
    
    # Verify all fields are present
    assert tx_view is not None, f"TX {tx_hash} should be found"
    assert "hash" in tx_view, "TX view should have hash"
    assert "from" in tx_view, "TX view should have from"
    assert "to" in tx_view, "TX view should have to"
    assert "nonce" in tx_view, "TX view should have nonce"
    assert "gas" in tx_view, "TX view should have gas"
    assert "value" in tx_view, "TX view should have value"
    
    # Verify values are correct
    assert tx_view["hash"] == tx_hash, "TX hash should match"
    assert tx_view["nonce"] == 0, "TX nonce should be 0"
    assert tx_view["value"] == 1_000_000_000, "TX value should be 1 ANM"
    assert tx_view["gas"] == 21000, "TX gas limit should be 21000"
    
    print(f"✓ CLI format transaction has full fields via getTransactionByHash")
    print(f"  Fields: {list(tx_view.keys())}")
    print(f"  from: {tx_view.get('from')}")
    print(f"  to: {tx_view.get('to')}")
    print(f"  value: {tx_view.get('value')}")


def test_cli_envelope_included_in_mined_block():
    """Test that CLI format pending tx is included when mining a block."""
    client, cfg, _ = new_test_client()
    
    raw_hex, tx_hash = _build_cli_format_tx(chain_id=cfg.chain_id)
    if raw_hex is None:
        return
    
    # Submit transaction
    rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    
    # Verify tx is pending
    pending_before = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending_before, "CLI format TX should be in pending pool"
    
    # Mine 1 block
    mine_result = rpc_call(client, "miner.mine", [1])["result"]
    assert mine_result["mined"] == 1, "Should mine exactly 1 block"
    
    # Get the mined block
    block_height = mine_result["height"]
    block = rpc_call(client, "chain.getBlockByNumber", [block_height, True])["result"]
    assert block is not None, f"Block at height {block_height} should exist"
    
    # Check if transaction is in the block
    block_txs = block.get("transactions", [])
    tx_hashes_in_block = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    
    assert tx_hash in tx_hashes_in_block, \
        f"CLI format TX {tx_hash} not found in block txs: {tx_hashes_in_block}"
    
    # Verify txsRoot is non-zero
    txs_root = block.get("transactionsRoot") or block.get("txsRoot")
    assert txs_root is not None, "Block should have txsRoot"
    assert txs_root != "0x" + ("00" * 32), "Block txsRoot should be non-zero"
    
    # Verify tx is no longer pending
    pending_after = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash not in pending_after, "TX should not be pending after mining"
    
    print(f"✓ CLI format transaction {tx_hash} included in block {block_height}")
    print(f"  TxsRoot: {txs_root}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
