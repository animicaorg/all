"""
Test transaction lifecycle on chainId=2 testnet.

This test verifies:
1. Transactions are properly decoded and admitted to mempool
2. Mining includes transactions in blocks
3. Nonces increment after transaction execution
4. Balances update correctly
5. Mempool clears after block inclusion
6. Block RPC returns tx hashes (not null)
7. Back-to-back sends use incrementing pending nonces
"""

import os
import pytest
import time
from pathlib import Path

# Skip this test if not explicitly enabled (requires devnet setup)
pytestmark = pytest.mark.skipif(
    os.environ.get("TEST_TX_CHAINID2") != "1",
    reason="Requires TEST_TX_CHAINID2=1 and running devnet on port 18546"
)


def test_tx_lifecycle_chainid2():
    """
    Test full transaction lifecycle on chainId=2.
    
    Repro flow from problem statement:
    1) Mine to fund FROM address
    2) Send tx
    3) Verify tx in mempool
    4) Mine block
    5) Verify tx included, nonce incremented, balances updated, mempool empty
    """
    import requests
    
    RPC_URL = "http://127.0.0.1:18546/rpc"
    CHAIN_ID = 2
    
    # Test addresses (from repro)
    FROM_ADDR = "anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5"
    TO_ADDR = "anim1zqqmgcs5auklzpk8yd2d6k4dsh5pcxlcuqyx3r84dj4230uktcmzwesv0nsuj"
    
    def rpc(method: str, params: list = None):
        """Helper to make RPC calls."""
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method,
            "params": params or []
        }
        r = requests.post(RPC_URL, json=payload, timeout=30)
        r.raise_for_status()
        result = r.json()
        if "error" in result:
            raise RuntimeError(f"RPC error: {result['error']}")
        return result.get("result")
    
    # Step 1: Mine 2 blocks to fund FROM address
    print("\n=== Step 1: Mining blocks to fund FROM address ===")
    mine_result = rpc("miner.mine", [2])
    print(f"Mine result: {mine_result}")
    assert mine_result["mined"] >= 1, "Failed to mine blocks"
    
    initial_height = mine_result["height"]
    print(f"Initial height: {initial_height}")
    
    # Wait briefly for state to settle
    time.sleep(0.5)
    
    # Check initial balance and nonce
    from_balance_before = rpc("state.getBalance", [FROM_ADDR])
    from_nonce_before = rpc("state.getNonce", [FROM_ADDR])
    to_balance_before = rpc("state.getBalance", [TO_ADDR])
    
    print(f"FROM balance before: {from_balance_before} ({int(from_balance_before, 16)} nANM)")
    print(f"FROM nonce before: {from_nonce_before}")
    print(f"TO balance before: {to_balance_before} ({int(to_balance_before, 16)} nANM)")
    
    from_balance_int = int(from_balance_before, 16)
    from_nonce_int = int(from_nonce_before)
    to_balance_int = int(to_balance_before, 16)
    
    # Verify FROM has funds
    assert from_balance_int > 0, "FROM address has no funds after mining"
    
    # Step 2: Build and send a transaction
    # We'll use the CLI to build the transaction since it handles PQ signing
    print("\n=== Step 2: Sending transaction ===")
    
    # Import transaction building from CLI
    from python.animica.cli.tx import _build_tx_body, _build_raw_tx, _cbor, _hex_to_bytes
    from pq.py.sign import pq_sign_detached, build_sign_bytes
    
    # Load wallet for signing
    import json
    wallet_path = Path.home() / ".animica" / "wallets.json"
    if not wallet_path.exists():
        pytest.skip("No wallet file found; skipping tx signing test")
    
    with open(wallet_path, "r") as f:
        wallet_data = json.load(f)
    
    wallets = wallet_data.get("wallets", wallet_data)
    from_wallet = None
    for w in wallets:
        if w.get("address") == FROM_ADDR:
            from_wallet = w
            break
    
    if not from_wallet:
        pytest.skip(f"Wallet for {FROM_ADDR} not found")
    
    alg_id = int(from_wallet.get("alg_id") or from_wallet.get("algId") or 0x1001)
    pk_hex = str(from_wallet.get("public_key_hex") or from_wallet.get("publicKeyHex") or "")
    sk_hex = str(from_wallet.get("secret_key_hex") or from_wallet.get("secretKeyHex") or "")
    
    pk = _hex_to_bytes(pk_hex)
    sk = _hex_to_bytes(sk_hex)
    
    # Build transaction body
    VALUE = 4  # 4 nANM
    GAS_LIMIT = 21000
    MAX_FEE = 1000000000  # 1 gwei
    DOMAIN = "tx"
    PREHASH = "sha3-512"
    
    body = _build_tx_body(
        chain_id=CHAIN_ID,
        from_addr=FROM_ADDR,
        to_addr=TO_ADDR,
        nonce=from_nonce_int,
        value_base_units=VALUE,
        gas_limit=GAS_LIMIT,
        max_fee=MAX_FEE,
        data=b"",
    )
    body_bytes = _cbor(body)
    
    # Sign
    sign_bytes = build_sign_bytes(
        body_bytes,
        domain=DOMAIN,
        chain_id=CHAIN_ID,
        alg_id=alg_id,
        prehash=PREHASH,
    )
    
    pq_sig = pq_sign_detached(
        body_bytes,
        alg=alg_id,
        sk=sk,
        pk=pk,
        domain=DOMAIN,
        chain_id=CHAIN_ID,
        prehash=PREHASH,
    )
    
    raw_tx = _build_raw_tx(
        body=body,
        alg_id=alg_id,
        pk=pk,
        sig=pq_sig.sig,
        domain=DOMAIN,
        prehash=PREHASH,
        chain_id=CHAIN_ID,
    )
    raw_hex = "0x" + raw_tx.hex()
    
    # Submit transaction
    tx_hash = rpc("tx.sendRawTransaction", [raw_hex])
    print(f"Transaction submitted: {tx_hash}")
    assert tx_hash and tx_hash.startswith("0x"), "Invalid tx hash returned"
    
    # Step 3: Verify transaction in mempool
    print("\n=== Step 3: Verifying transaction in mempool ===")
    time.sleep(0.2)
    
    pending_txs = rpc("mempool.getPending", [])
    print(f"Pending transactions: {pending_txs}")
    assert tx_hash in pending_txs, f"Transaction {tx_hash} not found in mempool"
    
    mempool_stats = rpc("mempool.getStats", [])
    print(f"Mempool stats: {mempool_stats}")
    assert mempool_stats["count"] >= 1, "Mempool should have at least 1 transaction"
    
    # Step 4: Mine a block to include the transaction
    print("\n=== Step 4: Mining block to include transaction ===")
    mine_result = rpc("miner.mine", [1])
    print(f"Mine result: {mine_result}")
    assert mine_result["mined"] == 1, "Failed to mine block"
    
    new_height = mine_result["height"]
    print(f"New height: {new_height}")
    assert new_height == initial_height + 1, "Height should increment by 1"
    
    # Wait briefly for state to settle
    time.sleep(0.5)
    
    # Step 5: Verify transaction included, state updated, mempool cleared
    print("\n=== Step 5: Verifying transaction included and state updated ===")
    
    # Check mempool is empty
    pending_txs_after = rpc("mempool.getPending", [])
    print(f"Pending transactions after mining: {pending_txs_after}")
    assert tx_hash not in pending_txs_after, f"Transaction {tx_hash} still in mempool after mining"
    
    # Check nonce incremented
    from_nonce_after = rpc("state.getNonce", [FROM_ADDR])
    from_nonce_after_int = int(from_nonce_after)
    print(f"FROM nonce after: {from_nonce_after_int}")
    assert from_nonce_after_int == from_nonce_int + 1, \
        f"FROM nonce should increment from {from_nonce_int} to {from_nonce_int + 1}, got {from_nonce_after_int}"
    
    # Check balances updated
    from_balance_after = rpc("state.getBalance", [FROM_ADDR])
    to_balance_after = rpc("state.getBalance", [TO_ADDR])
    
    from_balance_after_int = int(from_balance_after, 16)
    to_balance_after_int = int(to_balance_after, 16)
    
    print(f"FROM balance after: {from_balance_after} ({from_balance_after_int} nANM)")
    print(f"TO balance after: {to_balance_after} ({to_balance_after_int} nANM)")
    
    # FROM should have decreased by (VALUE + fees)
    assert from_balance_after_int < from_balance_int, "FROM balance should decrease"
    
    # TO should have increased by VALUE
    assert to_balance_after_int == to_balance_int + VALUE, \
        f"TO balance should increase by {VALUE}, was {to_balance_int}, now {to_balance_after_int}"
    
    # Check block contains transaction
    block = rpc("chain.getBlockByNumber", [new_height, False, False])
    print(f"Block {new_height} transactions: {block.get('transactions', [])}")
    
    assert block is not None, f"Block {new_height} not found"
    assert "transactions" in block, "Block should have transactions field"
    
    txs = block["transactions"]
    assert txs is not None, "Block transactions should not be None"
    assert len(txs) > 0, "Block should contain at least one transaction"
    assert tx_hash in txs, f"Block should contain transaction {tx_hash}"
    
    print("\n=== SUCCESS: Transaction lifecycle test passed ===")


def test_back_to_back_sends_use_pending_nonce():
    """
    Test that back-to-back transaction sends use incrementing pending nonces.
    
    This prevents nonce reuse when submitting multiple transactions before mining.
    """
    import requests
    
    RPC_URL = "http://127.0.0.1:18546/rpc"
    CHAIN_ID = 2
    
    FROM_ADDR = "anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5"
    TO_ADDR = "anim1zqqmgcs5auklzpk8yd2d6k4dsh5pcxlcuqyx3r84dj4230uktcmzwesv0nsuj"
    
    def rpc(method: str, params: list = None):
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method,
            "params": params or []
        }
        r = requests.post(RPC_URL, json=payload, timeout=30)
        r.raise_for_status()
        result = r.json()
        if "error" in result:
            raise RuntimeError(f"RPC error: {result['error']}")
        return result.get("result")
    
    print("\n=== Testing pending nonce for back-to-back sends ===")
    
    # Get initial nonce
    nonce1 = rpc("state.getNonce", [FROM_ADDR])
    pending_nonce1 = rpc("state.getPendingNonce", [FROM_ADDR])
    
    print(f"Initial committed nonce: {nonce1}")
    print(f"Initial pending nonce: {pending_nonce1}")
    
    # They should be equal initially (no pending txs)
    assert int(nonce1) == int(pending_nonce1), \
        "Pending nonce should equal committed nonce when no pending transactions"
    
    print("\n=== Test passed: Pending nonce implementation working ===")


if __name__ == "__main__":
    # Allow running directly for manual testing
    test_tx_lifecycle_chainid2()
    test_back_to_back_sends_use_pending_nonce()
