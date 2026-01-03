from __future__ import annotations

import pytest

from rpc.tests import new_test_client, rpc_call
from rpc.tests.test_tx_send_mempool_visibility import _choose_working_sig_alg


def _build_signed_transfer(cfg, sender_kp, recipient_addr: str, nonce: int, value: int, alg: str, sign_fn):
    from core.encoding.canonical import tx_sign_bytes
    from core.types.tx import Sig, Tx
    from pq.py.registry import ALG_ID

    tx = Tx.transfer(
        chain_id=cfg.chain_id,
        nonce=nonce,
        from_addr=sender_kp.address,
        to_addr=recipient_addr,
        value=value,
        gas_limit=21000,
        gas_price=0,
        data=b"",
        access_list=[],
    )

    sign_bytes = tx_sign_bytes(tx)
    sig_bytes = sign_fn(alg, sender_kp.secret_key, sign_bytes)
    sig_env = Sig(
        alg=ALG_ID[alg] if isinstance(alg, str) else alg,
        pub=sender_kp.public_key,
        sig=sig_bytes,
    )
    from dataclasses import replace
    tx_signed = replace(tx, sigs=(sig_env,))

    return "0x" + tx_signed.to_cbor().hex(), "0x" + tx_signed.txid().hex()


def test_send_raw_transaction_rejects_nonce_gap_and_not_pending() -> None:
    client, cfg, _ = new_test_client()

    try:
        from pq.py import keygen as pq_keygen
    except Exception:
        pytest.skip("PQ keygen not available")
        return

    alg, sender_kp, sign_fn, _verify_fn, _addr_from_pubkey = _choose_working_sig_alg()
    receiver_kp = pq_keygen.keygen(alg)

    raw_hex, tx_hash = _build_signed_transfer(
        cfg, sender_kp, receiver_kp.address, nonce=1, value=0, alg=alg, sign_fn=sign_fn
    )
    error_resp = rpc_call(
        client, "tx.sendRawTransaction", {"rawTx": raw_hex}, expect_error=True
    )
    err = error_resp["error"]
    assert isinstance(err.get("code"), int)
    assert "nonce" in (err.get("message") or "").lower()

    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash not in pending
