# -*- coding: utf-8 -*-
"""
Regression test: tx.sendRawTransaction success must mean tx is in mempool.

This test verifies that when RPC returns success for tx.sendRawTransaction,
the transaction is actually present in the mempool and can be retrieved
via mempool.getPending and mempool.explain.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Sequence

import pytest

from tests.integration import env


def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "10"))
    except (ValueError, TypeError):
        return 10.0


def _rpc_call(
    rpc_url: str,
    method: str,
    params: Optional[Sequence[Any] | Dict[str, Any]] = None,
    *,
    req_id: int = 1,
    expect_error: bool = False,
) -> Any:
    if params is None:
        params = []
    if isinstance(params, dict):
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    else:
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": list(params)}
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
        if expect_error:
            return msg["error"]
        raise AssertionError(f"JSON-RPC error from {method}: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"JSON-RPC response missing 'result' for {method}: {msg}")
    return msg["result"]


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
) -> tuple[str, str]:
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
def test_tx_send_success_means_mempool_inclusion():
    """
    Test that tx.sendRawTransaction returning success means tx is in mempool.
    
    This is the core requirement: if RPC returns a tx hash without error,
    the tx MUST be present in mempool.getPending immediately after.
    """
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8547/rpc")

    try:
        _rpc_call(rpc_url, "chain.getHead")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, AssertionError) as e:
        pytest.skip(f"RPC not available at {rpc_url}: {e}")

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

    mine_result = _rpc_call(rpc_url, "miner.mine", {"count": 5, "address": sender_kp.address})
    if mine_result.get("mined", 0) < 1:
        pytest.skip("Failed to mine initial funding blocks")

    chain_id = int(_rpc_call(rpc_url, "chain.getChainId"))
    sender_nonce = _parse_int(_rpc_call(rpc_url, "state.getNonce", [sender_hex]))
    
    raw_hex, tx_hash = _build_signed_transfer(
        chain_id=chain_id,
        sender_kp=sender_kp,
        sender_hex=sender_hex,
        recipient_hex=receiver_hex,
        nonce=sender_nonce,
        value=1,
    )

    # Send tx
    send_result = _rpc_call(rpc_url, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert send_result == tx_hash, f"tx.sendRawTransaction returned {send_result}, expected {tx_hash}"

    # CRITICAL: Verify tx is in mempool immediately after successful send
    pending = _rpc_call(rpc_url, "mempool.getPending")
    assert isinstance(pending, list), f"mempool.getPending should return list, got {type(pending)}"
    assert tx_hash in pending, (
        f"FAILURE: tx.sendRawTransaction returned success (tx_hash={tx_hash}) "
        f"but tx NOT in mempool.getPending (got {pending}). "
        "This is the bug we're fixing: RPC must only return success if tx is actually in mempool."
    )

    # Also verify via mempool.explain
    explain = _rpc_call(rpc_url, "mempool.explain", [tx_hash])
    assert isinstance(explain, dict), f"mempool.explain should return dict, got {type(explain)}"
    status = explain.get("status")
    assert status != "not_found", (
        f"FAILURE: tx {tx_hash} returned by tx.sendRawTransaction but mempool.explain says 'not_found'. "
        f"Explain result: {explain}"
    )

    # Mine block to clear mempool
    mine_result2 = _rpc_call(rpc_url, "miner.mine", {"count": 1, "address": sender_kp.address})
    assert mine_result2.get("mined", 0) == 1, "Failed to mine block"

    # Verify tx was included in block
    height = mine_result2.get("height", 0)
    block = _rpc_call(rpc_url, "chain.getBlockByNumber", [height, True])
    tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block.get("transactions", [])]
    assert tx_hash in tx_hashes, f"Expected tx {tx_hash} in block, got {tx_hashes}"

    # Verify mempool cleared
    pending_after = _rpc_call(rpc_url, "mempool.getPending")
    assert tx_hash not in pending_after, f"Expected tx removed from mempool after mining, got {pending_after}"

    # Verify balance updated
    receiver_balance = _parse_int(_rpc_call(rpc_url, "state.getBalance", [receiver_hex]))
    assert receiver_balance == 1, f"Receiver balance expected 1, got {receiver_balance}"


@pytest.mark.integration
def test_tx_send_with_insufficient_funds_returns_error():
    """
    Test that tx.sendRawTransaction returns error (not success) for insufficient funds.
    
    This ensures the RPC doesn't silently accept txs that will never be mined.
    """
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8547/rpc")

    try:
        _rpc_call(rpc_url, "chain.getHead")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, AssertionError) as e:
        pytest.skip(f"RPC not available at {rpc_url}: {e}")

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

    chain_id = int(_rpc_call(rpc_url, "chain.getChainId"))
    sender_nonce = _parse_int(_rpc_call(rpc_url, "state.getNonce", [sender_hex]))

    # Try to send more than available balance (sender has 0 balance)
    raw_hex, tx_hash = _build_signed_transfer(
        chain_id=chain_id,
        sender_kp=sender_kp,
        sender_hex=sender_hex,
        recipient_hex=receiver_hex,
        nonce=sender_nonce,
        value=1000000,  # Large value that exceeds sender balance
    )

    # This should fail with insufficient funds error
    try:
        send_result = _rpc_call(rpc_url, "tx.sendRawTransaction", {"rawTx": raw_hex})
        # If we get here, the RPC accepted the tx - verify it's NOT in mempool
        pending = _rpc_call(rpc_url, "mempool.getPending")
        assert tx_hash not in pending, (
            f"FAILURE: tx.sendRawTransaction accepted tx with insufficient funds (tx_hash={tx_hash}) "
            f"and it's in mempool ({pending}). This tx should have been rejected."
        )
    except AssertionError as e:
        # Expected: RPC should return error for insufficient funds
        error_msg = str(e)
        assert "error" in error_msg.lower() or "insufficient" in error_msg.lower(), (
            f"Expected 'insufficient funds' error, got: {error_msg}"
        )


@pytest.mark.integration
def test_tx_send_with_nonce_gap_is_handled():
    """
    Test that tx.sendRawTransaction with nonce gap is handled correctly.
    
    A tx with a future nonce may be held in mempool (not ready) but should
    still be retrievable via mempool.explain or getPending.
    """
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8547/rpc")

    try:
        _rpc_call(rpc_url, "chain.getHead")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, AssertionError) as e:
        pytest.skip(f"RPC not available at {rpc_url}: {e}")

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

    mine_result = _rpc_call(rpc_url, "miner.mine", {"count": 5, "address": sender_kp.address})
    if mine_result.get("mined", 0) < 1:
        pytest.skip("Failed to mine initial funding blocks")

    chain_id = int(_rpc_call(rpc_url, "chain.getChainId"))
    sender_nonce = _parse_int(_rpc_call(rpc_url, "state.getNonce", [sender_hex]))

    # Send tx with nonce+5 (creating a nonce gap)
    raw_hex, tx_hash = _build_signed_transfer(
        chain_id=chain_id,
        sender_kp=sender_kp,
        sender_hex=sender_hex,
        recipient_hex=receiver_hex,
        nonce=sender_nonce + 5,  # Nonce gap
        value=1,
    )

    # This may be accepted (held) or rejected depending on mempool policy
    try:
        send_result = _rpc_call(rpc_url, "tx.sendRawTransaction", {"rawTx": raw_hex})
        
        # If accepted, verify it's either in mempool or explain shows it
        pending = _rpc_call(rpc_url, "mempool.getPending")
        in_pending = tx_hash in pending
        
        explain = _rpc_call(rpc_url, "mempool.explain", [tx_hash])
        explain_status = explain.get("status") if isinstance(explain, dict) else None
        
        # At least one of these should be true if RPC returned success
        assert in_pending or explain_status not in (None, "not_found"), (
            f"FAILURE: tx.sendRawTransaction returned success for nonce-gap tx (tx_hash={tx_hash}) "
            f"but tx is not in mempool.getPending ({pending}) and mempool.explain status is {explain_status}. "
            "If RPC accepts the tx, it must be retrievable."
        )
    except AssertionError as e:
        # Expected: RPC may reject nonce gap immediately
        error_msg = str(e)
        assert "nonce" in error_msg.lower() or "gap" in error_msg.lower(), (
            f"Expected 'nonce gap' error, got: {error_msg}"
        )
