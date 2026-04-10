"""
Unit test: selected mempool entries should be coerced to core Tx objects.
"""

from __future__ import annotations

import hashlib

import pytest


def _build_cli_envelope_raw(chain_id: int, sender_addr: str, recipient_addr: str) -> bytes:
    try:
        import cbor2
        from pq.py import keygen, sign
        from pq.py.address import decode_address
        from pq.py.registry import ALG_ID
        from pq.py.sign import build_sign_bytes
        from core.genesis.loader import compute_chain_identity
    except Exception:
        pytest.skip("PQ signing or CBOR not available")
        return b""

    alg_name = "dilithium3"
    try:
        kp = keygen.keygen(alg_name)
    except Exception:
        pytest.skip("PQ keygen not available")
        return b""

    sender_record = decode_address(sender_addr)
    sender_bytes = bytes(sender_record.digest)[:32].ljust(32, b"\x00")
    recipient_record = decode_address(recipient_addr)
    recipient_bytes = bytes(recipient_record.digest)[:32].ljust(32, b"\x00")

    body = {
        "chainId": chain_id,
        "from": sender_bytes,
        "to": recipient_bytes,
        "value": 1_000_000_000,
        "nonce": 0,
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
        },
    }

    return cbor2.dumps(envelope, canonical=True)


def test_coerce_selected_txs_from_cli_envelope():
    from rpc.tests import new_test_client
    from rpc.methods import miner as miner_methods
    from rpc.methods import tx as tx_methods
    from pq.py import keygen

    client, cfg, _ = new_test_client()
    sender_kp = keygen.keygen("dilithium3")
    recipient_kp = keygen.keygen("dilithium3")
    raw_bytes = _build_cli_envelope_raw(cfg.chain_id, sender_kp.address, recipient_kp.address)
    if not raw_bytes:
        pytest.skip("Failed to build CLI envelope raw bytes")
        return

    decoded_tx, _decoded_obj = tx_methods._decode_tx(raw_bytes)  # type: ignore[attr-defined]
    tx_hash = "0x" + hashlib.sha3_256(raw_bytes).hexdigest()

    txs, included_hashes, dropped_counts, dropped_by_hash, dropped_details = (
        miner_methods._coerce_selected_txs(
            selected=[decoded_tx],
            selected_hashes=[tx_hash],
            pending_raw_by_hash={tx_hash: raw_bytes},
            decode_fn=tx_methods._decode_tx,  # type: ignore[attr-defined]
        )
    )

    assert len(txs) == 1
    assert included_hashes == [tx_hash]
    assert not dropped_counts
    assert not dropped_by_hash
    assert not dropped_details
