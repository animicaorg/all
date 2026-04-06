from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import p2p
from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService
from p2p.tests import free_port, tcp_multiaddr
from rpc.methods import sync as sync_methods

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"

os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")


def _make_deps(tmp_path: Path, name: str) -> tuple[P2PDeps, AsyncP2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, AsyncP2PDeps(sync_deps)


@pytest.mark.asyncio
async def test_sync_force_rpc_triggers_wakeup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps_sync, deps = _make_deps(tmp_path, "node_sync_force")

    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "node_sync_force" / "p2p"),
    )

    await node.start()
    p2p.register_service(node)
    try:
        dummy_ctx = SimpleNamespace(get_head=lambda: {"height": 0})
        monkeypatch.setattr(sync_methods.deps, "ensure_started", lambda: dummy_ctx)

        result = await sync_methods.sync_force()
        assert "peerCount" in result
        assert node._sync_wakeup.is_set()
    finally:
        p2p.clear_service()
        await node.stop()


@pytest.mark.asyncio
async def test_sync_force_rpc_reports_blocking_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DummySvc:
        _sync_wakeup = SimpleNamespace(set=lambda: None)

        def enable_sync(self, _enabled: bool = True) -> None:
            return None

        async def force_sync_with_cache(self, *, clear_cache: bool = False) -> dict[str, object]:
            return {
                "started": False,
                "phase": "IDLE",
                "clear_cache": clear_cache,
            }

        def peer_count(self) -> int:
            return 1

    dummy_ctx = SimpleNamespace(get_head=lambda: {"height": 2})
    monkeypatch.setattr(sync_methods, "_get_p2p_service", lambda: _DummySvc())
    monkeypatch.setattr(sync_methods, "_get_core_p2p_service", lambda: None)
    monkeypatch.setattr(sync_methods.deps, "ensure_started", lambda: dummy_ctx)

    result = await sync_methods.sync_force(clear_cache=True)
    assert result["success"] is False
    assert result["blockingReason"] == "sync_phase:idle"
