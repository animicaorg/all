import socket
from types import SimpleNamespace

import pytest


class DummyP2PConfig:
    listen_tcp = ("127.0.0.1", 30333)
    seeds = []
    max_outbound = 0
    max_inbound = 0


class DummyP2PDeps:
    @staticmethod
    def open(*_args, **_kwargs):
        return object()


class DummyP2PService:
    def __init__(self, **_kwargs):
        pass

    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 30333))
        finally:
            sock.close()


def _bind_conflicting_port() -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 30333))
    server.listen(1)
    return server


def _patch_p2p(monkeypatch) -> None:
    import p2p
    import p2p.config
    import p2p.deps
    import p2p.node.p2p_service

    monkeypatch.setattr(p2p.config, "load_config", lambda: DummyP2PConfig())
    monkeypatch.setattr(p2p.node.p2p_service, "P2PService", DummyP2PService)
    monkeypatch.setattr(p2p, "register_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(p2p.deps, "P2PDeps", DummyP2PDeps)
    monkeypatch.setattr(p2p.deps, "AsyncP2PDeps", lambda deps: deps)


@pytest.mark.asyncio
async def test_p2p_optional_on_port_conflict(monkeypatch):
    from rpc import deps

    server = _bind_conflicting_port()
    monkeypatch.setenv("ANIMICA_P2P_ENABLE", "1")
    monkeypatch.setenv("ANIMICA_P2P_REQUIRED", "0")
    monkeypatch.setenv("ANIMICA_P2P_CORE_ENABLE", "0")
    monkeypatch.setenv("P2P_LISTEN", "127.0.0.1:30333")
    _patch_p2p(monkeypatch)

    cfg = SimpleNamespace(
        db_uri="sqlite:///:memory:",
        chain_id=1,
        genesis_path=None,
        log_level="INFO",
        p2p_required=True,
    )

    try:
        ctx = await deps.startup(cfg)
        assert ctx.p2p_service is None
        assert ctx.p2p_enabled is False
    finally:
        server.close()
        await deps.shutdown()


@pytest.mark.asyncio
async def test_p2p_required_raises_on_port_conflict(monkeypatch):
    from rpc import deps

    server = _bind_conflicting_port()
    monkeypatch.setenv("ANIMICA_P2P_ENABLE", "1")
    monkeypatch.setenv("ANIMICA_P2P_REQUIRED", "1")
    monkeypatch.setenv("ANIMICA_P2P_CORE_ENABLE", "0")
    monkeypatch.setenv("P2P_LISTEN", "127.0.0.1:30333")
    _patch_p2p(monkeypatch)

    cfg = SimpleNamespace(
        db_uri="sqlite:///:memory:",
        chain_id=1,
        genesis_path=None,
        log_level="INFO",
        p2p_required=True,
    )

    try:
        with pytest.raises(RuntimeError):
            await deps.startup(cfg)
    finally:
        server.close()
        await deps.shutdown()
