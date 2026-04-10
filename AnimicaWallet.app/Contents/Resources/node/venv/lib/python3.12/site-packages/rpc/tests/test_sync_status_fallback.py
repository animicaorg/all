import pytest

from rpc.methods import sync as sync_methods
from rpc import deps


@pytest.mark.asyncio
async def test_sync_status_fallback_uses_chain_head(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyCtx:
        p2p_start_error = "init_failed: IndentationError"

        def get_head(self) -> dict[str, object]:
            return {"height": 6934, "hash": "0xdeadbeef"}

    monkeypatch.setattr(deps, "get_ctx", lambda: DummyCtx())
    monkeypatch.setattr(sync_methods, "_get_p2p_service", lambda: None)

    status = await sync_methods.sync_get_status()

    assert status["head_height"] == 6934
    assert status["best_block_height"] == 6934
    assert status["chain_head_height"] == 6934
    assert status["p2p_init_failed"] is True
