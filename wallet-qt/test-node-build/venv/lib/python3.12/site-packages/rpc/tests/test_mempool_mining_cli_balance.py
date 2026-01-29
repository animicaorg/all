"""
Integration test: CLI-style envelope tx should be mined, evicted, and update balances.
"""

from __future__ import annotations

import hashlib

import pytest

from rpc.tests import new_test_client, rpc_call


def _parse_balance(result_value: str | int) -> int:
    if isinstance(result_value, str):
        return int(result_value, 16) if result_value.startswith("0x") else int(result_value)
    return int(result_value)


def _build_cli_envelope(
    *,
    chain_id: int,
    sender_addr: str,
    recipient_addr: str,
    nonce: int,
    value: int,
) -> tuple[str, str]:
    try:
        import cbor2
        from pq.py import keygen, sign
        from pq.py.address import decode_address
        from pq.py.registry import ALG_ID
        from pq.py.sign import build_sign_bytes
        from core.genesis.loader import compute_chain_identity
    except Exception:
        pytest.skip("PQ signing or CBOR not available")
        return "", ""

    alg_name = "dilithium3"
    try:
        kp = keygen.keygen(alg_name)
    except Exception:
        pytest.skip("PQ keygen not available")
        return "", ""

    sender_record = decode_address(sender_addr)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    recipient_record = decode_address(recipient_addr)
    recipient_bytes = bytes(recipient_record.digest)[:32].ljust(32, b"\x00")

    body = {
        "chainId": chain_id,
        "from": sender_bytes,
        "to": recipient_bytes,
        "value": int(value),
        "nonce": int(nonce),
        "gasLimit": 21000,
        "maxFee": 1,
        "data": b"",
    }

    body_bytes = cbor2.dumps(body, canonical=True)
    fork_id = compute_chain_identity(None, chain_id=chain_id).fork_id
    alg_id = ALG_ID[alg_name]
    sign_bytes = build_sign_bytes(
        body_bytes,
        domain="tx",
        chain_id=chain_id,
        fork_id=fork_id,
        alg_id=alg_id,
        prehash="sha3-512",
    )
    sig_env = sign.sign_detached(
        sign_bytes,
        alg_id,
        kp.secret_key,
        domain="tx",
        chain_id=chain_id,
        fork_id=fork_id,
        prehash="sha3-512",
        pk=kp.public_key,
    )

    envelope = {
        "body": body,
        "sig": {
            "algId": alg_id,
            "pk": kp.public_key,
            "sig": sig_env.sig,
            "domain": "tx",
            "prehash": "sha3-512",
            "chainId": chain_id,
        },
    }

    cbor_bytes = cbor2.dumps(envelope, canonical=True)
    raw_hex = "0x" + cbor_bytes.hex()
    tx_hash = "0x" + hashlib.sha3_256(cbor_bytes).hexdigest()
    return raw_hex, tx_hash


def test_cli_envelope_mined_updates_balance_and_evicts():
    client, cfg, _ = new_test_client()

    try:
        from pq.py import keygen
    except Exception:
        pytest.skip("PQ keygen not available")
        return

    sender_kp = keygen.keygen("dilithium3")
    recipient_kp = keygen.keygen("dilithium3")

    mine_result = rpc_call(
        client, "miner.mine", {"count": 1, "address": sender_kp.address}
    )["result"]
    assert mine_result["mined"] == 1

    initial_balance_result = rpc_call(
        client, "state.getBalance", [recipient_kp.address]
    )["result"]
    initial_balance = _parse_balance(initial_balance_result)

    raw_hex, tx_hash = _build_cli_envelope(
        chain_id=cfg.chain_id,
        sender_addr=sender_kp.address,
        recipient_addr=recipient_kp.address,
        nonce=0,
        value=1_000_000_000,
    )

    submit = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert submit["result"] == tx_hash

    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending

    mine_result = rpc_call(
        client, "miner.mine", {"count": 1, "address": sender_kp.address}
    )["result"]
    assert mine_result["mined"] == 1

    block_height = mine_result["height"]
    block = rpc_call(client, "chain.getBlockByNumber", [block_height, True])["result"]
    block_txs = block.get("transactions", [])
    tx_hashes_in_block = [
        tx.get("hash") if isinstance(tx, dict) else tx for tx in block_txs
    ]
    assert tx_hash in tx_hashes_in_block

    pending_after = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash not in pending_after

    final_balance_result = rpc_call(
        client, "state.getBalance", [recipient_kp.address]
    )["result"]
    final_balance = _parse_balance(final_balance_result)
    assert final_balance >= initial_balance + 1_000_000_000
