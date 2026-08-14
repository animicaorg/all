from __future__ import annotations

import json

import pytest

from mempool.pool import Pool, PoolConfig
from mempool.errors import PersistenceFailed
from rpc.mempool_service import MempoolService


def _build_tx(chain_id: int = 1):
    from core.types.tx import Tx

    tx = Tx.transfer(
        chain_id=chain_id,
        nonce=0,
        gas_price=1,
        gas_limit=21000,
        sender=b"\x11" * 32,
        to=b"\x22" * 32,
        amount=1,
        version=1,
    )
    raw = tx.to_cbor()
    tx_hash = "0x" + tx.txid().hex()
    return tx, raw, tx_hash


def _new_service(tmp_path) -> MempoolService:
    pool = Pool(cfg=PoolConfig())
    return MempoolService(
        pool=pool,
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
        persist_enabled=True,
        persist_ttl_s=600,
    )


def test_sendraw_persists_to_mempool(tmp_path) -> None:
    service = _new_service(tmp_path)
    tx, raw, tx_hash = _build_tx()

    service.submit(tx=tx, raw=raw, local=True)

    persist_path = tmp_path / "mempool" / "pending.jsonl"
    assert persist_path.exists(), "Expected pending.jsonl to be created after submit"
    entries = [json.loads(line) for line in persist_path.read_text().splitlines() if line.strip()]
    assert any(entry.get("hash") == tx_hash for entry in entries)


def test_persistence_failure_is_not_replay(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _new_service(tmp_path)
    tx, raw, tx_hash = _build_tx()

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service, "_persist_snapshot", boom)

    with pytest.raises(PersistenceFailed):
        service.submit(tx=tx, raw=raw, local=True)

    assert service.has_hash(tx_hash) is False
    rejection = service.get_rejection(tx_hash)
    assert rejection is not None
    assert rejection.get("reason") == "persistence_failed"

    with pytest.raises(PersistenceFailed):
        service.submit(tx=tx, raw=raw, local=True)
