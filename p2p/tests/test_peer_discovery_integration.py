from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

import p2p
from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService
from p2p.tests import free_port, tcp_multiaddr
from rpc.methods import p2p as rpc_p2p

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"

os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
os.environ.setdefault("ANIMICA_P2P_ENABLE_DNS_SEEDS", "0")


def _make_deps(tmp_path: Path, name: str) -> tuple[P2PDeps, AsyncP2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, AsyncP2PDeps(sync_deps)


async def _wait_for_peers(node: P2PService, count: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if node.peer_count() >= count:
            return True
        await asyncio.sleep(0.1)
    return False


def _mine_blocks(sync_deps: P2PDeps, count: int) -> None:
    height, head_hash = sync_deps.head()
    header = sync_deps.header_by_hash(head_hash) if head_hash else None
    if header is None:
        header = sync_deps.header_by_number(0)
    assert header is not None

    timestamp = int(getattr(header, "timestamp", 0))
    for _ in range(count):
        timestamp += 1
        child = header.build_child(
            timestamp=timestamp,
            state_root=header.stateRoot,
            txs_root=ZERO32,
            receipts_root=ZERO32,
            proofs_root=ZERO32,
            da_root=ZERO32,
            nonce=0,
            extra=b"",
        )
        block = Block(header=child, txs=(), proofs=(), receipts=None)
        ok, reason = sync_deps.import_block(block)
        assert ok, reason
        header = child


async def _wait_for_height(
    deps: AsyncP2PDeps, height: int, timeout: float = 20.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur, _ = await deps.head()
        if cur >= height:
            return True
        await asyncio.sleep(0.2)
    return False


def _register_rpc_service(service: P2PService) -> None:
    p2p.register_service(service)
    rpc_p2p._p2p_service = None


@pytest.mark.asyncio
async def test_runtime_peer_injection_connects(tmp_path: Path) -> None:
    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b")

    addr_a = tcp_multiaddr(free_port())
    addr_b = tcp_multiaddr(free_port())

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b" / "p2p"),
    )

    await node_a.start()
    await node_b.start()
    try:
        _register_rpc_service(node_b)
        result = await rpc_p2p.add_peers([addr_a])
        assert result.get("success") is True
        assert await _wait_for_peers(node_b, 1)
    finally:
        p2p.clear_service()
        rpc_p2p._p2p_service = None
        await node_b.stop()
        await node_a.stop()


@pytest.mark.asyncio
async def test_gossip_addr_relay_expands_peer_set(tmp_path: Path) -> None:
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
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b" / "p2p"),
    )
    node_c = P2PService(
        listen_addrs=[addr_c],
        seeds=[],
        chain_id=deps_c_sync.chain_id,
        deps=deps_c,
        peerstore_path=str(tmp_path / "node_c" / "p2p"),
    )

    await node_a.start()
    await node_c.start()
    await node_b.start()
    try:
        await node_a.dial(addr_c)
        assert await _wait_for_peers(node_a, 1)

        _register_rpc_service(node_b)
        await rpc_p2p.add_peers([addr_a])
        assert await _wait_for_peers(node_b, 1)

        assert await _wait_for_peers(node_b, 2, timeout=15.0)
    finally:
        p2p.clear_service()
        rpc_p2p._p2p_service = None
        await node_b.stop()
        await node_c.stop()
        await node_a.stop()


@pytest.mark.asyncio
async def test_p2p_sync_converges_without_bootstrap_rpc(tmp_path: Path) -> None:
    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b")

    addr_a = tcp_multiaddr(free_port())
    addr_b = tcp_multiaddr(free_port())

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b" / "p2p"),
    )

    await node_a.start()
    await node_b.start()
    try:
        _mine_blocks(deps_a_sync, 12)
        _register_rpc_service(node_b)
        await rpc_p2p.add_peers([addr_a])
        assert await _wait_for_peers(node_b, 1)
        assert await _wait_for_height(deps_b, 12, timeout=30.0)
    finally:
        p2p.clear_service()
        rpc_p2p._p2p_service = None
        await node_b.stop()
        await node_a.stop()
