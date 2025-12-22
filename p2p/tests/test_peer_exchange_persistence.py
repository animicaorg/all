from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService
from p2p.tests import free_port, tcp_multiaddr

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"

os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
os.environ.setdefault("ANIMICA_P2P_ENABLE_DNS_SEEDS", "0")


def _make_deps(tmp_path: Path, name: str) -> tuple[P2PDeps, AsyncP2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, AsyncP2PDeps(sync_deps)


async def _wait_for_peerstore_addr(
    node: P2PService, addr: str, timeout: float = 10.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        addrs = [a for _, a, _ in node.peerstore.list_addresses(limit=200)]
        if addr in addrs:
            return True
        await asyncio.sleep(0.2)
    return False


async def _wait_for_peers(node: P2PService, count: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if node.peer_count() >= count:
            return True
        await asyncio.sleep(0.2)
    return False


@pytest.mark.asyncio
async def test_peer_exchange_persists_and_reconnects(tmp_path: Path) -> None:
    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b")
    deps_c_sync, deps_c = _make_deps(tmp_path, "node_c")

    addr_a = tcp_multiaddr(free_port())
    addr_b = tcp_multiaddr(free_port())
    addr_c = tcp_multiaddr(free_port())

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a" / "p2p"),
    )
    node_c = P2PService(
        listen_addrs=[addr_c],
        seeds=[addr_a],
        chain_id=deps_c_sync.chain_id,
        deps=deps_c,
        peerstore_path=str(tmp_path / "node_c" / "p2p"),
    )
    node_b_store = tmp_path / "node_b" / "p2p"
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[addr_a],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(node_b_store),
    )

    await node_a.start()
    await node_c.start()
    await node_b.start()
    try:
        assert await _wait_for_peers(node_b, 1)
        canonical_c = f"tcp://127.0.0.1:{addr_c.rsplit('/', 1)[-1]}"
        assert await _wait_for_peerstore_addr(node_b, canonical_c)
    finally:
        await node_b.stop()
        await node_c.stop()
        await node_a.stop()

    node_b_restart = P2PService(
        listen_addrs=[addr_b],
        seeds=[],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(node_b_store),
    )
    await node_b_restart.start()
    try:
        assert await _wait_for_peers(node_b_restart, 1)
    finally:
        await node_b_restart.stop()
