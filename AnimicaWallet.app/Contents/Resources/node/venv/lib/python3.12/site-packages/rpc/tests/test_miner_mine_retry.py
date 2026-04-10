from __future__ import annotations

from typing import Any


def test_miner_mine_retries_until_target(monkeypatch) -> None:
    import rpc.methods.miner as miner

    class FakeCtx:
        def __init__(self) -> None:
            self.height = 0
            self.params: dict[str, Any] = {}

        def get_head(self) -> dict[str, Any]:
            return {"height": self.height, "hash": "0xabc"}

    ctx = FakeCtx()

    outcomes = [False, False, True, True, True]

    def fake_mine_once(**_kwargs):
        result = outcomes.pop(0)
        if result:
            ctx.height += 1
            return (True, 10, {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}})
        return (False, 0, {})

    monkeypatch.setattr(miner, "_ctx", lambda: ctx)
    monkeypatch.setattr(miner, "_mine_once", fake_mine_once)
    monkeypatch.setattr(miner, "_mining_gate", lambda **_kw: (True, None))
    monkeypatch.setenv("ANIMICA_MINER_MINE_RETRY_DELAY_S", "0")
    monkeypatch.setenv("ANIMICA_MINER_MINE_MAX_FAILURES", "0")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")

    result = miner.miner_mine(count=3)
    assert result["mined"] == 3
