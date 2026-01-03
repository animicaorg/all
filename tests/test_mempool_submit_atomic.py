from __future__ import annotations

from unittest.mock import Mock

import pytest

from mempool.errors import PersistenceFailed
from mempool.pool import Pool, PoolConfig
from rpc.mempool_service import MempoolService


def _build_tx(sender_bytes: bytes, nonce: int, *, chain_id: int = 1337) -> tuple[dict, bytes, str]:
    from core.encoding.cbor import dumps as cbor_dumps
    from core.utils.hash import sha3_256

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
    tx_hash = "0x" + sha3_256(raw_bytes).hex()
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
