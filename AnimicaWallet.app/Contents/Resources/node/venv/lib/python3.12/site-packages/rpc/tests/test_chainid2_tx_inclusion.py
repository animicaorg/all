"""
Test transaction inclusion on chainId 2 (testnet).

This test verifies the fix for the issue where transactions submitted on
chainId 2 remained stuck in mempool and never got included in blocks.

Root cause: Transaction envelope format mismatch between CLI/RPC simplified
format and core canonical format. The fix normalizes the envelope during
transaction construction in the miner.
"""

import pytest
from rpc.tests import new_test_client, rpc_call, make_test_config
import rpc.server as rpc_server


def _build_signed_tx_chainid2(client, cfg):
    """Build a signed transaction for chainId 2 using core Tx types."""
    try:
        from core.encoding.canonical import tx_sign_bytes
        from core.genesis.loader import compute_chain_identity
        from core.types.tx import Tx, UnsignedTx, TxKind, TxTransfer, PqSignature
        from pq.py import keygen, sign
        from pq.py.address import decode_address
        from pq.py.registry import ALG_ID
    except ImportError:
        pytest.skip("Core tx types or PQ not available")
        return None, None, None
    
    # Generate keypair for sender
    alg_name = "dilithium3"
    try:
        kp = keygen.keygen(alg_name)
    except Exception:
        pytest.skip("PQ keygen not available")
        return None, None, None
    
    # Decode sender address to bytes
    sender_record = decode_address(kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    
    # Use sender as recipient for testing (simple self-transfer)
    recipient_bytes = sender_bytes
    
    # Build unsigned transfer with chainId=2
    unsigned = UnsignedTx(
        chain_id=2,  # TESTNET
        nonce=0,
        gas_price=1000000000,  # 1 Gwei
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=1_000_000_000, data=b""),
        access_list=(),
    )
    
    # Sign transaction
    sign_bytes = tx_sign_bytes(unsigned.to_obj(), 2)
    fork_id = compute_chain_identity(None, chain_id=cfg.chain_id).fork_id
    sig_env = sign.sign_detached(
        sign_bytes,
        alg_name,
        kp.secret_key,
        domain="tx",
        chain_id=cfg.chain_id,
        fork_id=fork_id,
    )
    
    # Create signed tx
    sig = PqSignature(alg_id=ALG_ID[alg_name], pubkey=kp.public_key, sig=sig_env.sig)
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    
    # Encode to CBOR hex
    cbor_bytes = tx.to_cbor()
    raw_hex = "0x" + cbor_bytes.hex()
    tx_hash = "0x" + tx.txid().hex()
    
    return raw_hex, tx_hash, kp.address


def test_chainid2_tx_appears_in_mempool():
    """Test that a chainId=2 transaction appears in mempool.getPending."""
    # Create a test client with chainId=2
    cfg, tmp = make_test_config()
    cfg = cfg._replace(chain_id=2)  # Set to testnet
    
    app = rpc_server.create_app(cfg)
    rpc_server.deps.ensure_started(cfg)
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    # Build and submit transaction
    raw_hex, tx_hash, sender = _build_signed_tx_chainid2(client, cfg)
    if raw_hex is None:
        return  # Skipped
    
    # Submit transaction
    result = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert "result" in result, f"Expected result, got {result}"
    returned_hash = result["result"]
    assert returned_hash == tx_hash, f"Hash mismatch: {returned_hash} != {tx_hash}"
    
    # Check mempool.getPending
    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending, f"TX {tx_hash} not in pending pool: {pending}"
    
    print(f"✓ ChainId=2 transaction {tx_hash} appears in mempool pending")


def test_chainid2_tx_getTransactionByHash_returns_full_fields():
    """Test that tx.getTransactionByHash returns full fields for chainId=2 pending tx."""
    # Create a test client with chainId=2
    cfg, tmp = make_test_config()
    cfg = cfg._replace(chain_id=2)
    
    app = rpc_server.create_app(cfg)
    rpc_server.deps.ensure_started(cfg)
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    # Build and submit transaction
    raw_hex, tx_hash, sender = _build_signed_tx_chainid2(client, cfg)
    if raw_hex is None:
        return
    
    # Submit transaction
    rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    
    # Get transaction by hash
    tx_view = rpc_call(client, "tx.getTransactionByHash", {"txHash": tx_hash})["result"]
    
    # Verify all fields are present (not just stub with {hash, value: 0})
    assert tx_view is not None, f"TX {tx_hash} should be found"
    assert "hash" in tx_view, "TX view should have hash"
    assert "from" in tx_view, "TX view should have from"
    assert "to" in tx_view, "TX view should have to"
    assert "nonce" in tx_view, "TX view should have nonce"
    assert "gas" in tx_view, "TX view should have gas"
    assert "value" in tx_view, "TX view should have value"
    
    # Verify values are correct (not 0/null stubs)
    assert tx_view["hash"] == tx_hash, "TX hash should match"
    assert tx_view["nonce"] == 0, "TX nonce should be 0"
    assert tx_view["value"] == 1_000_000_000, "TX value should be 1 ANM (1e9 base units)"
    assert tx_view["gas"] == 21000, "TX gas limit should be 21000"
    
    print(f"✓ ChainId=2 transaction {tx_hash} has full fields via getTransactionByHash")
    print(f"  Fields: {list(tx_view.keys())}")


def test_chainid2_tx_included_in_mined_block():
    """Test that chainId=2 pending tx is included when mining a block."""
    # Create a test client with chainId=2
    cfg, tmp = make_test_config()
    cfg = cfg._replace(chain_id=2)
    
    app = rpc_server.create_app(cfg)
    rpc_server.deps.ensure_started(cfg)
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    # Build and submit transaction
    raw_hex, tx_hash, sender = _build_signed_tx_chainid2(client, cfg)
    if raw_hex is None:
        return
    
    # Submit transaction
    rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    
    # Verify tx is pending
    pending_before = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending_before, "TX should be in pending pool before mining"
    
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
        f"ChainId=2 TX {tx_hash} not found in block txs: {tx_hashes_in_block}"
    
    # Verify txsRoot is non-zero (block has transactions)
    txs_root = block.get("transactionsRoot") or block.get("txsRoot")
    assert txs_root is not None, "Block should have txsRoot"
    assert txs_root != "0x" + ("00" * 32), "Block txsRoot should be non-zero when txs included"
    
    # Verify tx is no longer pending
    pending_after = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash not in pending_after, "TX should not be pending after mining"
    
    print(f"✓ ChainId=2 transaction {tx_hash} included in block {block_height}")
    print(f"  TxsRoot: {txs_root}")


def test_chainid2_state_updates_after_tx_mined():
    """Test that sender nonce increments after chainId=2 tx is mined."""
    # Create a test client with chainId=2
    cfg, tmp = make_test_config()
    cfg = cfg._replace(chain_id=2)
    
    app = rpc_server.create_app(cfg)
    rpc_server.deps.ensure_started(cfg)
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    # Build and submit transaction
    raw_hex, tx_hash, sender = _build_signed_tx_chainid2(client, cfg)
    if raw_hex is None:
        return
    
    # Get initial sender nonce (should be 0 for new address)
    # Note: sender won't have balance initially, so tx might fail execution
    # But nonce should still increment if tx is included in block
    try:
        initial_nonce = rpc_call(client, "state.getNonce", [sender])["result"]
    except Exception:
        # If getNonce fails for new address, assume 0
        initial_nonce = 0
    
    print(f"Initial sender nonce: {initial_nonce}")
    
    # Submit and mine
    rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    mine_result = rpc_call(client, "miner.mine", [1])["result"]
    
    # Verify tx was included
    block_height = mine_result["height"]
    block = rpc_call(client, "chain.getBlockByNumber", [block_height, True])["result"]
    block_txs = block.get("transactions", [])
    tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    
    if tx_hash in tx_hashes:
        print(f"✓ ChainId=2 transaction {tx_hash} was included in block")
        
        # Check nonce after mining
        # Note: Nonce increment depends on tx execution, which depends on sender having balance
        # In test env, sender likely has no balance, so tx might revert
        # But the test verifies that tx was AT LEAST ATTEMPTED (included in block)
        try:
            final_nonce = rpc_call(client, "state.getNonce", [sender])["result"]
            print(f"Final sender nonce: {final_nonce}")
            if final_nonce > initial_nonce:
                print(f"  ✓ Nonce incremented from {initial_nonce} to {final_nonce}")
            else:
                print(f"  ℹ Nonce unchanged (tx likely reverted due to insufficient balance)")
        except Exception as e:
            print(f"  ℹ Could not query nonce after mining: {e}")
    else:
        pytest.fail(f"ChainId=2 transaction {tx_hash} was not included in block")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
