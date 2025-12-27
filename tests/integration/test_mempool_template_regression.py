# -*- coding: utf-8 -*-
"""
Regression: mining includes pending mempool txs and clears the pool.
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
def test_mempool_pending_tx_gets_mined_and_cleared():
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

    mine_result = _rpc_call(rpc_url, "miner.mine", {"count": 1, "address": sender_kp.address})
    if mine_result.get("mined", 0) == 0:
        pytest.skip("Failed to mine initial funding block")

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
    send_result = _rpc_call(rpc_url, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert send_result == tx_hash, f"tx.sendRawTransaction returned {send_result}, expected {tx_hash}"

    pending = _rpc_call(rpc_url, "mempool.getPending")
    assert tx_hash in pending, f"Expected tx in mempool, got {pending}"

    explain = _rpc_call(rpc_url, "mempool.explain", [tx_hash])
    assert explain.get("status") in {"eligible", "rejected"}, f"Unexpected explain status: {explain}"

    mine_result2 = _rpc_call(rpc_url, "miner.mine", {"count": 1, "address": sender_kp.address})
    assert mine_result2.get("mined", 0) == 1, "Failed to mine second block"
    height = mine_result2.get("height", 0)

    block = _rpc_call(rpc_url, "chain.getBlockByNumber", [height, True])
    tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in block.get("transactions", [])]
    assert tx_hash in tx_hashes, f"Expected tx {tx_hash} in block, got {tx_hashes}"

    pending_after = _rpc_call(rpc_url, "mempool.getPending")
    assert pending_after == [], f"Expected mempool to be empty, got {pending_after}"

    receiver_balance = _parse_int(_rpc_call(rpc_url, "state.getBalance", [receiver_hex]))
    assert receiver_balance == 1, f"Receiver balance expected 1, got {receiver_balance}"
