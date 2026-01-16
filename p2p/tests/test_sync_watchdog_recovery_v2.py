from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState, _SyncHeader, _SyncRequest
from p2p.tests import free_port, tcp_multiaddr

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_deps(tmp_path: Path, name: str) -> P2PDeps:
    db_path = tmp_path / f"{name}.db"
    return P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))


def _register_peer(node: P2PService, remote: str) -> _PeerState:
    session = node._peer_registry.register(remote, "inbound")
    peer = _PeerState(
        session_id=session.session_id,
        remote=remote,
        direction="inbound",
        conn=None,
        stream=None,
        framer=None,
        write_lock=asyncio.Lock(),
    )
    peer.ready_for_sync = True
    peer.peer_id = f"{remote}-id"
    peer.hello_done.set()
    peer.hello = {
        "version": "2",
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "genesis_header_hash": node._genesis_header_hash(),
        "genesis_block_hash": node._genesis_block_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync", "blocks", "headers"],
        "head_height": 1,
        "head_hash": node._genesis_hash(),
    }
    node._peers[remote] = peer
    node._peers_by_session[peer.session_id] = peer
    node._update_peer_head_table(peer, height=1, source="test")
    return peer


@pytest.mark.asyncio
async def test_dead_peer_timeout_requeues_and_rotates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _make_deps(tmp_path, "dead-peer-timeout")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "dead-peer-timeout" / "p2p"),
    )
    peer_a = _register_peer(node, "peer:A")
    peer_b = _register_peer(node, "peer:B")
    block_hash = b"\x11" * 32
    started_at = time.time() - 30
    node._sync_inflight_blocks[block_hash] = started_at
    node._sync_inflight_peers[block_hash] = peer_a.remote
    node._sync_inflight_block_requests[block_hash] = _SyncRequest(
        request_id="req-dead",
        peer_id=peer_a.remote,
        kind="blocks",
        started_at=started_at,
        deadline_at=started_at + 1,
        retry_count=0,
        item_hash=block_hash,
    )

    node._expire_inflight_blocks()

    assert block_hash in node._sync_block_queue_set
    assert node._sync_timeouts_by_peer.get(peer_a.remote) == 1

    monkeypatch.setattr(node, "_send", AsyncMock())
    queued = await node._schedule_block_requests(peer=peer_b)
    assert queued == 1
    assert node._sync_inflight_peers.get(block_hash) == peer_b.remote


def test_missing_parent_backfill_and_resolution(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, "missing-parent")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "missing-parent" / "p2p"),
    )
    peer = _register_peer(node, "peer:orphan")
    orphan_hash = b"\x22" * 32
    parent_hash = b"\x33" * 32
    sync_block = SimpleNamespace(
        block=None,
        hash=orphan_hash,
        parent_hash=parent_hash,
        origin_peer=peer.remote,
        received_at=time.time(),
    )
    node._buffer_orphan_block(sync_block)
    node._handle_missing_parent(peer, sync_block)

    assert parent_hash in node._sync_block_queue_set
    node._resolve_orphan_waiting(parent_hash)
    assert orphan_hash in node._sync_block_queue_set


@pytest.mark.asyncio
async def test_cache_orphan_quarantine_fetches_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _make_deps(tmp_path, "cache-orphan")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "cache-orphan" / "p2p"),
    )
    block_hash = b"\x44" * 32
    parent_hash = b"\x55" * 32

    class _Cache:
        def __init__(self) -> None:
            self.invalidated: list[bytes] = []

        def get_block(self, _block_hash: bytes) -> bytes:
            return b"raw"

        def invalidate_block(self, block_hash: bytes) -> None:
            self.invalidated.append(block_hash)

        def cache_size_bytes(self) -> int:
            return 0

        def cache_entries(self) -> int:
            return 0

    node._sync_cache = _Cache()

    fake_block = SimpleNamespace(
        block=SimpleNamespace(header=SimpleNamespace(parentHash=parent_hash, height=2)),
        hash=block_hash,
        parent_hash=parent_hash,
        received_at=0.0,
        origin_peer="sync-cache",
    )
    monkeypatch.setattr(node, "_decode_block", lambda _raw: fake_block)
    monkeypatch.setattr(node, "_has_block", lambda _hash: False)
    monkeypatch.setattr(node, "_import_block_payload", AsyncMock(return_value=(False, "missing parent")))

    ok = await node._try_import_cached_block(block_hash)
    assert ok is False
    assert block_hash in node._sync_cache.invalidated
    assert parent_hash in node._sync_block_queue_set


def test_same_height_fork_triggers_header_retry(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, "same-height-fork")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "same-height-fork" / "p2p"),
    )
    peer = _register_peer(node, "peer:fork")
    peer.hello["head_hash"] = b"\xaa" * 32
    node._handle_same_height_fork(peer, 5, "0x" + (b"\xbb" * 32).hex())
    assert node._sync_header_retry_queue
    assert node._sync_last_recovery_action == "fork_same_height"


def test_watchdog_replan_pipeline_on_stall(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, "watchdog-replan")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "watchdog-replan" / "p2p"),
    )
    _register_peer(node, "peer:tip")
    genesis_hash = node._genesis_hash()
    header1 = _SyncHeader(hash=b"\x01" * 32, parent_hash=genesis_hash, height=1, theta_micro=0, timestamp=0)
    header2 = _SyncHeader(hash=b"\x02" * 32, parent_hash=header1.hash, height=2, theta_micro=0, timestamp=0)
    node._sync_headers[header1.hash] = header1
    node._sync_headers[header2.hash] = header2
    node._sync_best_header = header2
    node._sync_watchdog_attempts = 3
    node._sync_last_progress_at = time.time() - (node._sync_stall_timeout + 5)
    node._sync_watchdog_last_progress_at = time.time() - (node._sync_watchdog_timeout + 5)

    now = time.time()
    node._sync_watchdog_check(now=now, head_height=0, head_hash=None)

    assert node._sync_last_recovery_action == "watchdog_replan_pipeline"
    assert node._sync_block_queue_set
