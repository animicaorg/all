from __future__ import annotations

import pytest

from core.encoding.canonical import tx_sign_bytes
from core.types.tx import Sig, Tx
from pq.py import keygen as pq_keygen
from pq.py import sign as pq_sign
from pq.py.registry import ALG_ID, normalize_alg_name

from rpc.tests import new_test_client, rpc_call


def _choose_working_sig_alg():
    for name in ("dilithium3", "sphincs_shake_128s"):
        alg = normalize_alg_name(name)
        try:
            kp = pq_keygen.keygen(alg)
            return alg, kp
        except Exception:
            continue
    pytest.skip("No working PQ signature backend available")


def _build_signed_transfer_for(*, chain_id: int, kp, to_addr: str, nonce: int, alg: str, value: int) -> tuple[bytes, str]:
    tx = Tx.transfer(
        chain_id=chain_id,
        nonce=nonce,
        from_addr=kp.address,
        to_addr=to_addr,
        value=value,
        gas_limit=21000,
        gas_price=1,
        data=b"",
        access_list=[],
    )
    sb = tx_sign_bytes(tx)
    sig_env = pq_sign.sign_detached(sb, alg, kp.secret_key, domain="tx/sign")
    sig = Sig(alg=ALG_ID[alg], pub=kp.public_key, sig=sig_env.sig)
    from dataclasses import replace

    tx_signed = replace(tx, sigs=(sig,))
    return tx_signed.to_cbor(), "0x" + tx_signed.txid().hex()


def _balance(client, address: str) -> int:
    raw = rpc_call(client, "state.getBalance", [address])["result"]
    return int(raw, 16) if isinstance(raw, str) and raw.startswith("0x") else int(raw)


def test_insufficient_pending_funds_does_not_evict_existing_tx() -> None:
    client, cfg, _ = new_test_client()

    alg, sender_kp = _choose_working_sig_alg()
    _, recipient_kp = _choose_working_sig_alg()

    mine = rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})["result"]
    assert mine["mined"] == 1

    balance = _balance(client, sender_kp.address)
    assert balance > 100_000

    value1 = balance - 21_010
    raw1, txh1 = _build_signed_transfer_for(
        chain_id=cfg.chain_id,
        kp=sender_kp,
        to_addr=recipient_kp.address,
        nonce=0,
        alg=alg,
        value=value1,
    )
    submit1 = rpc_call(client, "tx.sendRawTransaction", {"rawTx": "0x" + raw1.hex()})
    assert submit1["result"] == txh1

    pending1 = rpc_call(client, "mempool.getPending", {})["result"]
    assert txh1 in pending1

    raw2, txh2 = _build_signed_transfer_for(
        chain_id=cfg.chain_id,
        kp=sender_kp,
        to_addr=recipient_kp.address,
        nonce=1,
        alg=alg,
        value=50_000,
    )

    res2 = rpc_call(client, "tx.sendRawTransaction", {"rawTx": "0x" + raw2.hex()})
    assert "error" in res2
    err = res2["error"]
    payload = err.get("data", {}).get("mempoolError", err.get("data", {}))
    reason = str(payload.get("reason", ""))
    assert "insufficient" in reason

    pending2 = rpc_call(client, "mempool.getPending", {})["result"]
    assert txh1 in pending2
    assert txh2 not in pending2
