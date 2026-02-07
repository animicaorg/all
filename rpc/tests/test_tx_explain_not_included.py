from __future__ import annotations

from rpc.methods import tx as tx_methods


def test_tx_explain_not_included_delegates_to_mempool_explain(monkeypatch):
    expected = {"hash": "0x12", "status": "rejected", "reason": "fee_too_low"}

    def fake_explain(tx_hash: str):
        assert tx_hash == "0x12"
        return expected

    monkeypatch.setattr("rpc.methods.mempool.mempool_explain", fake_explain)
    assert tx_methods.tx_explain_not_included("0x12") == expected
