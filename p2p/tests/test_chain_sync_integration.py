from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState
from p2p.tests import free_port, tcp_multiaddr
from p2p.wire.encoding import encode_payload
from p2p.wire.messages import Blocks

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"

os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")


def _make_deps(tmp_path: Path, name: str) -> tuple[P2PDeps, AsyncP2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, AsyncP2PDeps(sync_deps)


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


async def _wait_for_peers(node: P2PService, count: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if node.peer_count() >= count:
            return True
        await asyncio.sleep(0.1)
    return False


async def _wait_for_header_responses(
    node: P2PService, timeout: float = 10.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = node.sync_status_snapshot()
        if status.last_header_response_count > 0:
            return True
        await asyncio.sleep(0.2)
    return False


@pytest.mark.asyncio
async def test_two_nodes_sync_from_genesis(tmp_path: Path) -> None:
    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a_two")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b_two")

    port_a = free_port()
    port_b = free_port()

    addr_a = tcp_multiaddr(port_a)
    addr_b = tcp_multiaddr(port_b)

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a_two" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[addr_a],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b_two" / "p2p"),
    )

    await node_a.start()
    await node_b.start()
    try:
        _mine_blocks(deps_a_sync, 3)
        await node_b.dial(addr_a)
        assert await _wait_for_peers(node_b, 1)
        assert await _wait_for_height(deps_b, 3, timeout=20.0)
        assert await _wait_for_header_responses(node_b, timeout=10.0)
    finally:
        await node_a.stop()
        await node_b.stop()


@pytest.mark.asyncio
async def test_three_nodes_converge_headers_first(tmp_path: Path) -> None:
    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b")
    deps_c_sync, deps_c = _make_deps(tmp_path, "node_c")

    port_a = free_port()
    port_b = free_port()
    port_c = free_port()

    addr_a = tcp_multiaddr(port_a)
    addr_b = tcp_multiaddr(port_b)

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[addr_a],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b" / "p2p"),
    )
    node_c = P2PService(
        listen_addrs=[tcp_multiaddr(port_c)],
        seeds=[addr_b],
        chain_id=deps_c_sync.chain_id,
        deps=deps_c,
        peerstore_path=str(tmp_path / "node_c" / "p2p"),
    )

    await node_a.start()
    try:
        _mine_blocks(deps_a_sync, 50)

        await node_b.start()
        await node_b.dial(addr_a)
        assert await _wait_for_peers(node_b, 1)
        assert await _wait_for_height(deps_b, 50, timeout=30.0)

        await node_c.start()
        await node_c.dial(addr_b)
        assert await _wait_for_peers(node_c, 1)
        assert await _wait_for_height(deps_c, 50, timeout=30.0)

        _mine_blocks(deps_a_sync, 5)
        assert await _wait_for_height(deps_b, 55, timeout=30.0)
        assert await _wait_for_height(deps_c, 55, timeout=30.0)

        _, head_hash_a = deps_a_sync.head()
        _, head_hash_b = deps_b_sync.head()
        _, head_hash_c = deps_c_sync.head()
        assert head_hash_a == head_hash_b == head_hash_c
    finally:
        await node_a.stop()
        await node_b.stop()
        await node_c.stop()


@pytest.mark.asyncio
async def test_invalid_block_penalizes_peer_and_sync_continues(
    tmp_path: Path,
) -> None:
    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a_bad")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b_bad")

    port_a = free_port()
    port_b = free_port()

    addr_a = tcp_multiaddr(port_a)
    addr_b = tcp_multiaddr(port_b)

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a_bad" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[addr_a],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b_bad" / "p2p"),
    )

    await node_a.start()
    await node_b.start()
    try:
        _mine_blocks(deps_a_sync, 5)
        await node_b.dial(addr_a)
        assert await _wait_for_peers(node_b, 1)
        assert await _wait_for_height(deps_b, 5, timeout=20.0)

        bad_peer = _PeerState(
            session_id="bad-peer",
            remote="bad-peer:0",
            direction="inbound",
            conn=None,
            stream=None,
            framer=None,
            write_lock=asyncio.Lock(),
        )
        payload = encode_payload(Blocks(blocks=[b"not-a-cbor-block"]))
        await node_b._handle_blocks(bad_peer, payload)

        penalties = node_b.sync_status_snapshot().peer_penalties
        assert penalties.get("bad-peer:0", 0) >= 1

        _mine_blocks(deps_a_sync, 2)
        await node_b.force_sync()
        assert await _wait_for_height(deps_b, 7, timeout=20.0)
    finally:
        await node_a.stop()
        await node_b.stop()
