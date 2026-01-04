from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from mempool.errors import PersistenceFailed
from mempool.pool import Pool, PoolConfig
from rpc.mempool_service import MempoolService
from rpc.tests import new_test_client, rpc_call


def _build_tx(sender_bytes: bytes, nonce: int, *, chain_id: int = 1337) -> tuple[dict, bytes, str]:
    from core.encoding.cbor import dumps as cbor_dumps
    from mempool.tx_hash import tx_hash_hex as compute_tx_hash_hex

    body = {
        "from": sender_bytes,
        "nonce": nonce,
        "gasLimit": 21000,
        "chainId": chain_id,
        "gasPrice": 1,
        "maxFee": 1,
        "validAfter": 0,
        "validUntil": 50,
        "salt": b"atomic-test",
    }
    tx_envelope = {"body": body}
    raw_bytes = cbor_dumps(tx_envelope)
    tx_dict = {"body": body, "raw": raw_bytes}
    tx_hash = compute_tx_hash_hex(raw_bytes)
    return tx_dict, raw_bytes, tx_hash


def test_persistence_failure_does_not_mark_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=1024 * 1024))
    state_db = Mock()
    state_db.get_nonce = Mock(return_value=0)
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,
        state_db=state_db,
        tx_index=None,
        persist_enabled=True,
    )

    monkeypatch.setattr(service, "_persist_snapshot", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    sender_bytes = bytes.fromhex("aa" * 32)
    tx_dict, raw_bytes, tx_hash = _build_tx(sender_bytes, nonce=0)

    with pytest.raises(PersistenceFailed):
        service.submit(tx=tx_dict, raw=raw_bytes, tx_hash_hex=tx_hash, local=True)

    assert tx_hash not in service._recent_txids
    assert not service.has_hash(tx_hash)
    rejection = service.get_rejection(tx_hash)
    assert rejection is not None
    assert rejection["reason"] == "persistence_failed"


def test_rpc_rejects_non_admitted_tx_across_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    genesis_path = Path(__file__).resolve().parents[1] / "core" / "genesis" / "mainnet.json"
    monkeypatch.setenv("ANIMICA_GENESIS_PATH", str(genesis_path))
    client, cfg, _tmpdir = new_test_client()

    from core.encoding.cbor import dumps as cbor_dumps
    from core.utils.address_codec import account_key_from_pubkey
    from mempool.tx_hash import tx_hash_hex as compute_tx_hash_hex
    from pq.py.registry import ALG_ID
    from rpc.methods import tx as tx_methods

    monkeypatch.setattr(tx_methods, "_verify_pq_signature", lambda *args, **kwargs: None)

    alg_id = ALG_ID["dilithium3"]
    pubkey = b"rpc-atomic-test-pubkey"
    sender_key = account_key_from_pubkey(pubkey, alg_id)

    body = {
        "from": sender_key,
        "nonce": 1,
        "gasLimit": 21000,
        "chainId": cfg.chain_id,
        "gasPrice": 0,
        "maxFee": 0,
        "validAfter": 0,
        "validUntil": 50,
        "salt": b"rpc-atomic",
    }
    envelope = {"body": body, "sig": {"algId": alg_id, "pubkey": pubkey, "sig": b"sig"}}
    raw_bytes = cbor_dumps(envelope)
    raw_hex = "0x" + raw_bytes.hex()
    tx_hash_hex = compute_tx_hash_hex(raw_bytes)

    rpc_call(
        client,
        "tx.sendRawTransaction",
        params={"rawTx": raw_hex},
        expect_error=True,
    )
    rpc_call(
        client,
        "tx_sendRawTransaction",
        params={"rawTx": raw_hex},
        expect_error=True,
    )

    pending_res = rpc_call(client, "mempool.getPending", params={})
    pending = pending_res["result"]
    assert tx_hash_hex not in pending
