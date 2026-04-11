from rpc.instant_tx import InstantTxService, set_instant_tx_service_singleton
from rpc.methods import tx as tx_methods


def test_tx_get_status_reports_pending_mempool_state(tmp_path, monkeypatch):
    svc = InstantTxService(data_root=tmp_path, chain_id=1, ttl_s=3600)
    set_instant_tx_service_singleton(svc)
    txid = "0x" + "66" * 32
    svc.emit_local(txid=txid, anchor_hash="0x" + "77" * 32, timestamp=2_200_000_000)

    monkeypatch.setattr(tx_methods, "_get_mempool_service", lambda: None)
    monkeypatch.setattr(tx_methods, "_pending_get", lambda _h: b"x")
    monkeypatch.setattr(tx_methods, "_lookup_persisted_tx", lambda _h: (None, None, None, None))
    monkeypatch.setattr(tx_methods, "_prune_reorged_txs", lambda: None)

    class _Ctx:
        def get_head(self):
            return {"height": 1}

    monkeypatch.setattr(tx_methods.deps, "ensure_started", lambda: _Ctx())

    out = tx_methods.tx_get_status(txid)
    assert out["status"] == "pending"
    assert out["state"] == "pending_mempool"
    assert out["instant_confirmed"] is False
    assert out["finalized_in_pow"] is False
    assert out["reason"] == "pending_mempool"


def test_tx_get_instant_receipt_not_found(monkeypatch):
    monkeypatch.setattr(tx_methods, "_instant_receipt", lambda _h: None)
    out = tx_methods.tx_get_instant_receipt("0x" + "88" * 32)
    assert out["status"] == "not_found"
