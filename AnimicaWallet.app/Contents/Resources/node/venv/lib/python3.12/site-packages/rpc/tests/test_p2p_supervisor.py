import asyncio
import contextlib
import time
from pathlib import Path

import pytest

from rpc import deps


class _DummyP2PService:
    def __init__(self) -> None:
        self._running = False
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1
        self._running = True

    async def stop(self) -> None:
        self.stopped += 1
        self._running = False


async def _wait_for(predicate, timeout: float = 0.25) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_p2p_supervisor_recovers_from_init_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}
    service = _DummyP2PService()

    def _fake_init_p2p_service(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return None, None, "init_failed: IndentationError", False, True
        return service, None, None, False, True

    monkeypatch.setattr(deps, "_init_p2p_service", _fake_init_p2p_service)
    monkeypatch.setenv("ANIMICA_P2P_SUPERVISOR_GRACE_S", "0")
    monkeypatch.setenv("ANIMICA_P2P_SUPERVISOR_CHECK_S", "0.01")
    monkeypatch.setenv("ANIMICA_P2P_SUPERVISOR_MAX_BACKOFF_S", "0.05")

    ctx = deps.RpcContext()
    ctx.cfg = object()
    ctx.data_root = Path(".")
    ctx.init_error = None
    ctx.p2p_service = None
    ctx.p2p_enabled = True
    ctx.p2p_required = False
    ctx.p2p_start_error = "init_failed: IndentationError"
    ctx.p2p_last_healthy_at = 0.0
    ctx.p2p_restart_backoff_s = 0.01

    task = asyncio.create_task(deps._p2p_supervisor(ctx))
    try:
        recovered = await _wait_for(lambda: ctx.p2p_service is service and service._running)
        assert recovered
        assert attempts["count"] >= 2
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
