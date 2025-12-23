from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService
from p2p.tests import free_port, tcp_multiaddr

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"

os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")


def _make_deps(tmp_path: Path, name: str) -> tuple[P2PDeps, AsyncP2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, AsyncP2PDeps(sync_deps)


@pytest.mark.asyncio
async def test_non_animica_peer_is_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_P2P_HANDSHAKE_TIMEOUT", "0.2")

    deps_sync, deps = _make_deps(tmp_path, "node_non_animica")
    listen_port = free_port()
    listen_addr = tcp_multiaddr(listen_port)

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(0.5)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, host="127.0.0.1", port=free_port())
    host, port = server.sockets[0].getsockname()[:2]
    peer_addr = f"/ip4/{host}/tcp/{port}"

    node = P2PService(
        listen_addrs=[listen_addr],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "node_non_animica" / "p2p"),
    )

    await node.start()
    try:
        await node.dial(peer_addr)
        await asyncio.sleep(0.1)
        status = node.sync_status_snapshot()
        assert status.eligible_peers_for_headers == []

        await asyncio.sleep(0.4)
        assert len(node._peers) == 0
    finally:
        await node.stop()
        server.close()
        await server.wait_closed()
