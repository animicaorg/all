from __future__ import annotations

from types import SimpleNamespace

from mempool.select import PendingTxEntry
from rpc.methods import mempool as mempool_methods


def test_mempool_explain_passes_current_head_height_to_selector(monkeypatch) -> None:
    tx_hash = "0x" + "12" * 32
    snapshot = SimpleNamespace(entries=[PendingTxEntry(hash_hex=tx_hash, raw=b"\xaa", tx=None)])

    class _MempoolService:
        def snapshot(self, limit: int = 1000):  # noqa: ARG002
            return snapshot

    captured: dict[str, object] = {}

    def _fake_select_for_block(**kwargs):
        captured["head_state"] = kwargs["head_state"]
        return SimpleNamespace(
            selected=[object()],
            selected_hashes=[tx_hash],
            rejected={},
            rejected_by_hash={},
            rejected_details_by_hash={},
        )

    monkeypatch.setattr(mempool_methods, "_get_mempool_service", lambda: _MempoolService())
    monkeypatch.setattr(mempool_methods, "_selection_head_height", lambda: 77)
    monkeypatch.setattr(mempool_methods, "select_for_block", _fake_select_for_block)
    monkeypatch.setattr(mempool_methods, "normalize_tx", lambda raw: raw)
    monkeypatch.setattr(mempool_methods, "tx_methods", None)
    monkeypatch.setattr(
        mempool_methods.deps,
        "get_ctx",
        lambda: SimpleNamespace(cfg=SimpleNamespace(chain_id=1), state_db=None, tx_index=None, params={}),
    )

    result = mempool_methods.mempool_explain(tx_hash)

    assert result["status"] == "eligible"
    assert isinstance(captured.get("head_state"), dict)
    assert captured["head_state"]["height"] == 77
