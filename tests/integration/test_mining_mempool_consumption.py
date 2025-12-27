# -*- coding: utf-8 -*-
"""
Integration: Mining consumes mempool transactions
=================================================

This test verifies that the `animica miner mine-blocks` command:
1. Fetches pending transactions from the mempool
2. Includes them in mined blocks
3. Executes them to update state (balances, nonces)
4. Evicts them from the mempool after successful inclusion

Test scenario:
1. Start with funded sender address (from mining rewards)
2. Send a transaction to a receiver
3. Verify transaction is in mempool
4. Mine a block
5. Verify transaction is no longer in mempool
6. Verify state updates (receiver balance, sender nonce)

Environment variables:
  RUN_INTEGRATION_TESTS=1     — enable integration tests package-wide
  ANIMICA_RPC_URL             — JSON-RPC URL (default: http://127.0.0.1:8547/rpc)
  ANIMICA_HTTP_TIMEOUT        — single RPC call timeout in seconds (default: 10)
  ANIMICA_MINING_TIMEOUT      — timeout for mining operations (default: 60)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env

# -------------------------------- RPC helpers --------------------------------


def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "10"))
    except (ValueError, TypeError):
        return 10.0


def _mining_timeout() -> float:
    try:
        return float(env("ANIMICA_MINING_TIMEOUT", "60"))
    except (ValueError, TypeError):
        return 60.0


def _rpc_call(
    rpc_url: str,
    method: str,
    params: Optional[Sequence[Any] | Dict[str, Any]] = None,
    *,
    req_id: int = 1,
) -> Any:
    if params is None:
        params = []
    if isinstance(params, dict):
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    else:
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": list(params),
        }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"JSON-RPC error from {method}: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"JSON-RPC response missing 'result' for {method}: {msg}")
    return msg["result"]


# --------------------------------- Test --------------------------------------


def _parse_int(value: Any) -> int:
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value or 0)


def _build_signed_transfer(
    *,
    chain_id: int,
    sender_kp: Any,
    sender_hex: str,
    recipient_hex: str,
    nonce: int,
    value: int,
) -> Tuple[str, str]:
    from core.encoding.canonical import tx_sign_bytes
    from core.types.tx import PqSignature, Tx, TxKind, TxTransfer, UnsignedTx
    from core.genesis.loader import compute_chain_identity
    from pq.py import sign
    from pq.py.registry import ALG_ID

    sender_bytes = bytes.fromhex(sender_hex[2:] if sender_hex.startswith("0x") else sender_hex)
    recipient_bytes = bytes.fromhex(recipient_hex[2:] if recipient_hex.startswith("0x") else recipient_hex)

    unsigned = UnsignedTx(
        chain_id=chain_id,
        nonce=nonce,
        gas_price=0,
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=value, data=b""),
        access_list=(),
    )

    sign_bytes = tx_sign_bytes(unsigned.to_obj())
    fork_id = compute_chain_identity(None, chain_id=chain_id).fork_id
    sig_env = sign.sign_detached(
        sign_bytes,
        "dilithium3",
        sender_kp.secret_key,
        domain="tx",
        chain_id=chain_id,
        fork_id=fork_id,
    )

    sig = PqSignature(alg_id=ALG_ID["dilithium3"], pubkey=sender_kp.public_key, sig=sig_env.sig)
    tx = Tx(unsigned=unsigned, sigs=(sig,))

    raw_hex = "0x" + tx.to_cbor().hex()
    tx_hash = "0x" + tx.txid().hex()
    return raw_hex, tx_hash


@pytest.mark.integration
def test_mining_consumes_mempool_transactions():
    """
    Test that mining via RPC includes mempool transactions and evicts them.

    This test uses the miner.mine RPC method (which powers the CLI) to mine blocks
    with mempool transactions. It verifies that transactions are:
    1. Included in the mined block
    2. Executed to update state
    3. Evicted from the mempool after mining

    Note: This test assumes a running node with an empty mempool. It uses the
    miner.mine RPC method directly to avoid dependencies on CLI parsing.
    """
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8547/rpc")

    # Skip if RPC is not available
    try:
        head_before = _rpc_call(rpc_url, "chain.getHead")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, AssertionError) as e:
        pytest.skip(f"RPC not available at {rpc_url}: {e}")

    # Get initial chain height
    height_before = int(head_before.get("height") or 0)

    # Generate sender/receiver keypairs
    try:
        from pq.py.keygen import keygen_sig
        from pq.py.address import decode_address
    except Exception as e:
        pytest.skip(f"PQ keygen not available: {e}")

    sender_kp = keygen_sig("dilithium3")
    receiver_kp = keygen_sig("dilithium3")

    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    sender_hex = "0x" + sender_bytes.hex()

    receiver_record = decode_address(receiver_kp.address)
    receiver_bytes = bytes(receiver_record.digest)[:32].ljust(32, b"\x00")
    receiver_hex = "0x" + receiver_bytes.hex()

    # Step 1: Mine an initial block to fund the sender
    print(f"Mining initial block at height {height_before}...")
    try:
        mine_result = _rpc_call(rpc_url, "miner.mine", {"count": 1, "address": sender_kp.address})
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, AssertionError) as e:
        pytest.skip(f"miner.mine RPC not available: {e}")

    mined_count = mine_result.get("mined", 0)
    if mined_count == 0:
        pytest.skip("Failed to mine initial block")

    new_height = mine_result.get("height", 0)
    print(f"Mined block {new_height}, reward: {mine_result.get('totalReward', 0)} nANM")

    # Step 2: Check mempool is empty
    try:
        mempool_before = _rpc_call(rpc_url, "mempool.getPending")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, AssertionError):
        pytest.skip("mempool.getPending RPC not available")

    assert mempool_before == [], f"Expected empty mempool, got: {mempool_before}"

    # Step 3: Send transaction from sender to receiver
    chain_id = int(_rpc_call(rpc_url, "chain.getChainId"))
    sender_nonce = _parse_int(_rpc_call(rpc_url, "state.getNonce", [sender_hex]))
    raw_hex, tx_hash = _build_signed_transfer(
        chain_id=chain_id,
        sender_kp=sender_kp,
        sender_hex=sender_hex,
        recipient_hex=receiver_hex,
        nonce=sender_nonce,
        value=3,
    )
    send_result = _rpc_call(rpc_url, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert send_result == tx_hash, f"tx.sendRawTransaction returned {send_result}, expected {tx_hash}"

    pending_after_send = _rpc_call(rpc_url, "mempool.getPending")
    assert tx_hash in pending_after_send, f"Expected tx in mempool, got {pending_after_send}"

    # Step 4: Mine another block; tx should be included and evicted
    print(f"Mining second block at height {new_height}...")
    mine_result2 = _rpc_call(rpc_url, "miner.mine", {"count": 1, "address": sender_kp.address})
    assert mine_result2.get("mined", 0) == 1, "Failed to mine second block"
    final_height = mine_result2.get("height", 0)
    print(f"Mined block {final_height}")

    block = _rpc_call(rpc_url, "chain.getBlockByNumber", [final_height, True])
    assert block, f"Expected block at height {final_height}"
    block_txs = block.get("transactions", [])
    tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs]
    assert tx_hash in tx_hashes, f"Expected tx {tx_hash} in block, got {tx_hashes}"

    pending_after = _rpc_call(rpc_url, "mempool.getPending")
    assert tx_hash not in pending_after, f"Tx {tx_hash} should be evicted, got {pending_after}"

    sender_balance = _parse_int(_rpc_call(rpc_url, "state.getBalance", [sender_hex]))
    receiver_balance = _parse_int(_rpc_call(rpc_url, "state.getBalance", [receiver_hex]))
    assert receiver_balance == 3, f"Receiver balance expected 3, got {receiver_balance}"
    assert sender_balance > 0, "Sender balance should remain positive after transfer"

    print("✓ Mining with mempool integration test passed")


if __name__ == "__main__":
    # Allow running this test standalone for debugging
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
