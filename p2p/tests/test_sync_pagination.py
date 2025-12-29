from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Tuple
from unittest.mock import AsyncMock

import pytest

from core.utils.hash import sha3_256
from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState
from p2p.tests import free_port, tcp_multiaddr
from p2p.wire.messages import HeaderCompact

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_deps(tmp_path: Path, name: str) -> Tuple[P2PDeps, AsyncP2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, AsyncP2PDeps(sync_deps)


def _make_header_batch(
    parent_hash: bytes, parent_timestamp: int, start_height: int, count: int
) -> Tuple[List[HeaderCompact], bytes, int]:
    headers: List[HeaderCompact] = []
    parent = parent_hash
    timestamp = parent_timestamp
    for i in range(count):
        height = start_height + i
        timestamp += 1
        h = sha3_256(parent + height.to_bytes(8, "big"))
        headers.append(
            HeaderCompact(
                hash=h,
                height=height,
                parent=parent,
                theta_micro=1,
                timestamp=timestamp,
            )
        )
        parent = h
    return headers, parent, timestamp


@pytest.mark.asyncio
async def test_sync_paginates_header_batches(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "pagination")
    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    genesis_timestamp = int(getattr(genesis, "timestamp", 0))

    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "pagination" / "p2p"),
    )

    peer = _PeerState(
        session_id="peer",
        remote="peer:0",
        direction="inbound",
        conn=None,
        stream=None,
        framer=None,
        write_lock=asyncio.Lock(),
    )
    peer.peer_id = "peer"
    peer.hello = {
        "head_height": 129,
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
    }
    peer.hello_done.set()
    peer.ready_for_sync = True
    node._peers[peer.remote] = peer
    eligible, ineligible = node._eligible_sync_peers()
    assert eligible, ineligible

    batch1, last_hash, last_ts = _make_header_batch(
        genesis.hash(), genesis_timestamp, 1, 128
    )
    batch2, _, _ = _make_header_batch(last_hash, last_ts, 129, 1)
    batches = [batch1, batch2, []]

    async def _fake_fetch_headers(_peer: _PeerState):
        return batches.pop(0)

    node._fetch_headers = AsyncMock(side_effect=_fake_fetch_headers)

    await node._sync_once()

    assert node._fetch_headers.call_count == 2
    assert node._sync_best_header is not None
    assert node._sync_best_header.height >= 129


@pytest.mark.asyncio
async def test_sync_schedules_blocks_after_multi_batch_headers(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "blocks")
    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    genesis_timestamp = int(getattr(genesis, "timestamp", 0))

    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "blocks" / "p2p"),
    )

    peer = _PeerState(
        session_id="peer",
        remote="peer:0",
        direction="inbound",
        conn=None,
        stream=None,
        framer=None,
        write_lock=asyncio.Lock(),
    )
    peer.peer_id = "peer"
    peer.hello = {
        "head_height": 256,
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
    }
    peer.hello_done.set()
    peer.ready_for_sync = True
    node._peers[peer.remote] = peer
    eligible, ineligible = node._eligible_sync_peers()
    assert eligible, ineligible

    batch1, last_hash, last_ts = _make_header_batch(
        genesis.hash(), genesis_timestamp, 1, 128
    )
    batch2, _, _ = _make_header_batch(last_hash, last_ts, 129, 128)
    batches = [batch1, batch2, []]

    async def _fake_fetch_headers(_peer: _PeerState):
        return batches.pop(0)

    node._fetch_headers = AsyncMock(side_effect=_fake_fetch_headers)

    result = await node._sync_once()

    assert node._sync_best_header is not None
    assert node._sync_best_header.height >= 256
    assert result.get("blocksRequested", 0) > 0
    assert len(node._sync_inflight_blocks) > 0


@pytest.mark.asyncio
async def test_sync_headers_surpasses_4096(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "surpass-4096")
    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    genesis_timestamp = int(getattr(genesis, "timestamp", 0))

    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "surpass-4096" / "p2p"),
    )

    peer = _PeerState(
        session_id="peer",
        remote="peer:0",
        direction="inbound",
        conn=None,
        stream=None,
        framer=None,
        write_lock=asyncio.Lock(),
    )
    peer.peer_id = "peer"
    peer.hello = {
        "head_height": 5000,
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
    }
    peer.hello_done.set()
    node._peers[peer.remote] = peer

    batches = []
    parent = genesis.hash()
    timestamp = genesis_timestamp
    height = 1
    remaining = 4097
    batch_size = node._sync_headers_batch
    while remaining > 0:
        count = min(batch_size, remaining)
        batch, parent, timestamp = _make_header_batch(parent, timestamp, height, count)
        batches.append(batch)
        height += count
        remaining -= count
    for batch in batches:
        accepted, err, _discarded = node._process_headers(peer, batch)
        assert err is None
        assert len(accepted) == len(batch)

    assert node._sync_best_header is not None
    assert node._sync_best_header.height > 4096


@pytest.mark.asyncio
async def test_sync_records_duplicate_headers(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "duplicate-headers")
    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    genesis_timestamp = int(getattr(genesis, "timestamp", 0))

    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "duplicate-headers" / "p2p"),
    )
    node._sync_headers_batch = 2

    peer = _PeerState(
        session_id="peer",
        remote="peer:0",
        direction="inbound",
        conn=None,
        stream=None,
        framer=None,
        write_lock=asyncio.Lock(),
    )
    peer.peer_id = "peer"
    peer.hello = {
        "head_height": 10,
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
    }
    peer.hello_done.set()
    node._peers[peer.remote] = peer

    batch, _, _ = _make_header_batch(genesis.hash(), genesis_timestamp, 1, 2)
    accepted, err, _discarded = node._process_headers(peer, batch)
    assert err is None
    assert len(accepted) == len(batch)

    accepted, err, _discarded = node._process_headers(peer, batch)
    assert accepted == []
    assert err is None

    snap = node.sync_status_snapshot()
    assert snap.headers_accepted_total == len(batch)
    assert snap.headers_seen_total == len(batch) * 2
