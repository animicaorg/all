# -*- coding: utf-8 -*-
"""
Integration: pending txs should appear in remote mempool before mining.

This test covers the real-world bug where node B never saw pending txs from node A
even though they were mined. It exercises RPC submission, P2P relay, and mempool
visibility across two connected nodes.
"""
from __future__ import annotations

import json
import time
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
        raise AssertionError(f"JSON-RPC error from {method}: {msg['error']}")
    return msg.get("result")


def _parse_int(value: Any) -> int:
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value or 0)


def _wait_for(predicate, timeout: float, interval: float = 0.2) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _pending_contains(pending: Any, tx_hash: str) -> bool:
    if isinstance(pending, list):
        for entry in pending:
            if isinstance(entry, str) and entry == tx_hash:
                return True
            if isinstance(entry, dict) and entry.get("hash") == tx_hash:
                return True
    return False


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
def test_remote_mempool_visibility_before_mining():
    rpc_a = env("ANIMICA_RPC_URL_A", "http://127.0.0.1:8545")
    rpc_b = env("ANIMICA_RPC_URL_B", "http://127.0.0.1:9545")

    try:
        _rpc_call(rpc_a, "chain.getHead")
        _rpc_call(rpc_b, "chain.getHead")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, AssertionError) as e:
        pytest.skip(f"RPC not available at {rpc_a} or {rpc_b}: {e}")

    try:
        from pq.py.keygen import keygen_sig
        from pq.py.address import decode_address
    except Exception as e:
        pytest.skip(f"PQ keygen not available: {e}")

    status_a = _rpc_call(rpc_a, "p2p.getStatus")
    listen_addrs = status_a.get("listen_addrs") if isinstance(status_a, dict) else None
    if not listen_addrs:
        pytest.skip("Node A does not expose listen_addrs; P2P likely disabled")

    _rpc_call(rpc_b, "p2p.addPeer", [listen_addrs[0]])
    connected = _wait_for(
        lambda: int(_rpc_call(rpc_a, "p2p.getStatus").get("peers_total", 0)) > 0
        and int(_rpc_call(rpc_b, "p2p.getStatus").get("peers_total", 0)) > 0,
        timeout=10.0,
        interval=0.5,
    )
    if not connected:
        pytest.skip("Nodes did not connect via P2P within timeout")

    sender_kp = keygen_sig("dilithium3")
    receiver_kp = keygen_sig("dilithium3")

    sender_record = decode_address(sender_kp.address)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    sender_hex = "0x" + sender_bytes.hex()

    receiver_record = decode_address(receiver_kp.address)
    receiver_bytes = bytes(receiver_record.digest)[:32].ljust(32, b"\x00")
    receiver_hex = "0x" + receiver_bytes.hex()

    mine_result = _rpc_call(rpc_a, "miner.mine", {"count": 2, "address": sender_kp.address})
    if mine_result.get("mined", 0) < 1:
        pytest.skip("Failed to mine initial funding blocks")

    chain_id = int(_rpc_call(rpc_a, "chain.getChainId"))
    sender_nonce = _parse_int(_rpc_call(rpc_a, "state.getNonce", [sender_hex]))
    raw_hex, tx_hash = _build_signed_transfer(
        chain_id=chain_id,
        sender_kp=sender_kp,
        sender_hex=sender_hex,
        recipient_hex=receiver_hex,
        nonce=sender_nonce,
        value=1,
    )

    send_result = _rpc_call(rpc_a, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert send_result == tx_hash, f"tx.sendRawTransaction returned {send_result}, expected {tx_hash}"

    seen_pending = _wait_for(
        lambda: _pending_contains(_rpc_call(rpc_b, "mempool.getPending", [True]), tx_hash),
        timeout=5.0,
        interval=0.5,
    )
    assert seen_pending, f"Remote mempool never showed pending tx {tx_hash}"

    mine_result2 = _rpc_call(rpc_a, "miner.mine", {"count": 1, "address": sender_kp.address})
    mined_height = int(mine_result2.get("height", 0))
    assert mined_height > 0, "Expected a mined block height"

    head_synced = _wait_for(
        lambda: _parse_int(_rpc_call(rpc_b, "chain.getHead").get("height", 0)) >= mined_height,
        timeout=20.0,
        interval=0.5,
    )
    assert head_synced, "Node B did not sync to mined height in time"

    mempool_cleared = _wait_for(
        lambda: not _pending_contains(_rpc_call(rpc_b, "mempool.getPending", [True]), tx_hash),
        timeout=10.0,
        interval=0.5,
    )
    assert mempool_cleared, f"Remote mempool still shows tx {tx_hash} after mining"

    balance_ok = _wait_for(
        lambda: _parse_int(_rpc_call(rpc_b, "state.getBalance", [receiver_hex])) >= 1,
        timeout=10.0,
        interval=0.5,
    )
    assert balance_ok, "Receiver balance did not update on node B after mining"
