from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import pytest

from core.chain.block_import import _theta_to_target, compute_header_hash
from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import P2PDeps
from p2p.node.p2p_service import (
    STALL_BLOCK_PEER_UNRESPONSIVE,
    STALL_BLOCK_TIMEOUT,
    STALL_CACHE_SHORT_CIRCUIT,
    P2PService,
    _PeerHeadInfo,
    _PeerState,
    _SyncRequest,
)
from p2p.tests import free_port, tcp_multiaddr
from p2p.wire.messages import HeaderCompact

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_deps(tmp_path: Path, name: str) -> tuple[P2PDeps, P2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, sync_deps


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
    peer.peer_id = "peer-test"
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
    node._update_peer_head_table(peer, height=1, source="test", head_hash=None)
    return peer


def _make_child_block(parent) -> Block:
    timestamp = int(getattr(parent, "timestamp", 0)) + 1
    target = _theta_to_target(int(getattr(parent, "thetaMicro", 0)))
    child = None
    for nonce in range(0, 10000):
        candidate = parent.build_child(
            timestamp=timestamp,
            state_root=parent.stateRoot,
            txs_root=ZERO32,
            receipts_root=ZERO32,
            proofs_root=ZERO32,
            da_root=ZERO32,
            nonce=nonce,
            extra=b"",
        )
        header_hash = compute_header_hash(candidate)
        if int.from_bytes(header_hash, "big") <= target:
            child = candidate
            break
    if child is None:
        raise AssertionError("Failed to find nonce meeting pow target for test block")
    return Block(header=child, txs=(), proofs=(), receipts=None)


class _NullCache:
    def get_block(self, _block_hash: bytes) -> None:
        return None

    def put_block(self, _block_hash: bytes, _raw: bytes, **_kwargs) -> None:
        return None

    def invalidate_block(self, _block_hash: bytes) -> None:
        return None


class _BadCache:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def get_block(self, _block_hash: bytes) -> bytes:
        return self._payload

    def put_block(self, _block_hash: bytes, _raw: bytes, **_kwargs) -> None:
        return None

    def invalidate_block(self, _block_hash: bytes) -> None:
        return None


@pytest.mark.asyncio
async def test_sync_loop_wakeup_schedules_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps_sync, deps = _make_deps(tmp_path, "sync-loop-wakeup")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "sync-loop-wakeup" / "p2p"),
    )
    _register_peer(node, "peer:1001")

    event = asyncio.Event()

    async def _fake_fetch_headers(_peer: _PeerState):
        event.set()
        return []

    monkeypatch.setattr(node, "_fetch_headers", _fake_fetch_headers)
    node._sync_tick_sec = 0.05
    node._running = True
    task = asyncio.create_task(node._sync_loop())
    try:
        node._request_sync(reason="test")
        await asyncio.wait_for(event.wait(), timeout=1.0)
    finally:
        node._running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_sync_loop_marks_stall_when_best_header_unknown_but_target_ahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    deps_sync, deps = _make_deps(tmp_path, "sync-loop-unknown-best-header-stall")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "sync-loop-unknown-best-header-stall" / "p2p"),
    )
    peer = _register_peer(node, "peer:1011")
    peer.hello["head_height"] = 5
    node._update_peer_head_table(peer, height=5, source="test", head_hash=None)
    node._local_head = lambda: (3, None)  # type: ignore[method-assign]
    node._sync_best_header = None
    node._sync_target_height = 5
    node._sync_last_head_height = 3
    node._sync_last_head_hash = None
    node._sync_last_progress_at = time.time() - node._sync_stall_timeout - 1.0
    async def _fake_sync_once(*, force: bool = False):
        node._running = False
        return {"started": force}

    monkeypatch.setattr(node, "_sync_once", _fake_sync_once)
    node._sync_tick_sec = 0.01
    node._running = True

    await node._sync_loop()

    assert any(
        "Sync stalled: headers == blocks with no progress" in record.message
        for record in caplog.records
    )
    assert node._sync_requested is False


@pytest.mark.asyncio
async def test_sync_loop_does_not_mark_stall_without_target_ahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    deps_sync, deps = _make_deps(tmp_path, "sync-loop-at-tip-no-stall")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "sync-loop-at-tip-no-stall" / "p2p"),
    )
    peer = _register_peer(node, "peer:1012")
    peer.hello["head_height"] = 3
    node._update_peer_head_table(peer, height=3, source="test", head_hash=None)
    node._local_head = lambda: (3, None)  # type: ignore[method-assign]
    node._sync_best_header = None
    node._sync_target_height = 3
    node._sync_last_head_height = 3
    node._sync_last_head_hash = None
    node._sync_last_progress_at = time.time() - node._sync_stall_timeout - 1.0
    async def _fake_sync_once(*, force: bool = False):
        node._running = False
        return {"started": force}

    monkeypatch.setattr(node, "_sync_once", _fake_sync_once)
    node._sync_tick_sec = 0.01
    node._running = True

    await node._sync_loop()

    assert not any(
        "Sync stalled: headers == blocks with no progress" in record.message
        for record in caplog.records
    )


def test_header_peer_selection_ignores_stale_high_peer(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "sync-select-stale-high-header")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "sync-select-stale-high-header" / "p2p"),
    )
    stale_peer = _register_peer(node, "peer:stale-high")
    active_peer = _register_peer(node, "peer:active-lower")
    stale_peer.peer_id = "peer-stale-high"
    active_peer.peer_id = "peer-active-lower"
    stale_peer.hello["head_height"] = 100
    active_peer.hello["head_height"] = 50
    now = time.time()
    node._sync_peer_heads[stale_peer.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now - node._sync_peer_head_stale_sec - 1.0,
        source="test",
    )
    node._sync_peer_heads[active_peer.remote] = _PeerHeadInfo(
        height=50,
        updated_at=now,
        source="test",
    )
    node._local_head = lambda: (49, None)  # type: ignore[method-assign]

    selected = node._select_sync_peer()

    assert selected is active_peer


def test_block_peer_selection_ignores_stale_high_peer(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "sync-select-stale-high-block")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "sync-select-stale-high-block" / "p2p"),
    )
    stale_peer = _register_peer(node, "peer:stale-high")
    active_peer = _register_peer(node, "peer:active-lower")
    stale_peer.peer_id = "peer-stale-high"
    active_peer.peer_id = "peer-active-lower"
    stale_peer.hello["head_height"] = 100
    active_peer.hello["head_height"] = 50
    now = time.time()
    node._sync_peer_heads[stale_peer.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now - node._sync_peer_head_stale_sec - 1.0,
        source="test",
    )
    node._sync_peer_heads[active_peer.remote] = _PeerHeadInfo(
        height=50,
        updated_at=now,
        source="test",
    )
    node._local_head = lambda: (49, None)  # type: ignore[method-assign]

    selected = node._select_block_peer(needed_height=50)

    assert selected is active_peer


@pytest.mark.asyncio
async def test_sync_loop_preserves_new_sync_request_raised_during_sync_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps_sync, deps = _make_deps(tmp_path, "sync-loop-preserve-new-request")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "sync-loop-preserve-new-request" / "p2p"),
    )
    _register_peer(node, "peer:1013")

    node._sync_requested = True
    node._sync_requested_at = time.time() - 5.0

    async def _fake_sync_once(*, force: bool = False):
        assert force is True
        node._sync_kick(reason="test-requeue", aggressive=True)
        node._running = False
        return {"started": True}

    async def _fake_schedule_block_requests(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(node, "_sync_once", _fake_sync_once)
    monkeypatch.setattr(
        node, "_schedule_block_requests", _fake_schedule_block_requests
    )
    node._sync_tick_sec = 0.01
    node._running = True

    await node._sync_loop()

    assert node._sync_requested is True


def test_aggressive_sync_boost_is_faster_than_normal_tick(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "sync-boost-fast")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "sync-boost-fast" / "p2p"),
    )
    node._sync_tick_sec = 0.005

    node._sync_kick(reason="test-aggressive-boost", aggressive=True)

    assert node._sync_boost_tick_sec is not None
    assert node._sync_boost_tick_sec < node._sync_tick_sec
    assert node._sync_boost_tick_sec <= 0.001


def test_explicit_sync_boost_tick_never_slows_normal_tick(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "sync-boost-explicit")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "sync-boost-explicit" / "p2p"),
    )
    node._sync_tick_sec = 0.005

    result = node.boost_sync(duration_s=1.0, tick_ms=50)

    assert result["boosted"] is True
    assert node._sync_boost_tick_sec == node._sync_tick_sec


def test_hash_normalization_handles_mixed_case(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "hash-normalization")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "hash-normalization" / "p2p"),
    )
    peer = _register_peer(node, "peer:1002")
    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    child_hash = compute_header_hash(child_block.header)
    parent_hash = genesis.hash()

    mixed_hex = parent_hash.hex().upper()
    node._local_head = lambda: (0, f"0X{mixed_hex}")

    headers = [
        HeaderCompact(
            hash=child_hash,
            height=1,
            parent=parent_hash,
            theta_micro=int(getattr(child_block.header, "thetaMicro", 0)),
            timestamp=int(getattr(child_block.header, "timestamp", 0)),
        )
    ]
    accepted, err, _discarded = node._process_headers(peer, headers)
    assert err is None
    assert accepted
    assert node._sync_last_matched_ancestor_height == 0
    assert node._sync_last_matched_ancestor_hash == parent_hash


def test_network_best_height_snapshot(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "network-best-height")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "network-best-height" / "p2p"),
    )
    peer_a = _register_peer(node, "peer:2001")
    peer_b = _register_peer(node, "peer:2002")
    peer_a.hello["head_height"] = 12
    peer_b.hello["head_height"] = 25
    node._update_peer_head_table(peer_a, height=12, source="test", head_hash=None)
    node._update_peer_head_table(peer_b, height=25, source="test", head_hash=None)

    snapshot = node.sync_status_snapshot()
    assert snapshot.network_best_height == 25


def test_inflight_block_expiry_requeues(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "inflight-block-expiry")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "inflight-block-expiry" / "p2p"),
    )
    peer = _register_peer(node, "peer:3001")
    block_hash = b"\x01" * 32
    started_at = time.time() - 10
    node._sync_inflight_blocks[block_hash] = started_at
    node._sync_inflight_peers[block_hash] = peer.remote
    node._sync_inflight_block_requests[block_hash] = _SyncRequest(
        request_id="req-1",
        peer_id=peer.remote,
        kind="blocks",
        started_at=started_at,
        deadline_at=started_at + 1,
        retry_count=0,
        item_hash=block_hash,
    )

    node._expire_inflight_blocks()

    assert block_hash in node._sync_block_queue_set
    assert node._sync_requested
    assert peer.sync_timeouts == 1


@pytest.mark.asyncio
async def test_queue_block_requests_uses_persisted_retry_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps_sync, deps = _make_deps(tmp_path, "queue-block-request-retry-count")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "queue-block-request-retry-count" / "p2p"),
    )
    peer = _register_peer(node, "peer:3010")
    block_hash = b"\x21" * 32
    node._sync_block_retry_counts[block_hash] = 2

    async def _fake_send(_peer: _PeerState, _msg_id, _payload) -> None:
        return None

    monkeypatch.setattr(node, "_send", _fake_send)

    requested = await node._queue_block_requests(peer, [block_hash])

    assert requested == 1
    request = node._sync_inflight_block_requests.get(block_hash)
    assert request is not None
    assert request.retry_count == 2


def test_inflight_block_retry_count_accumulates_and_resets(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "inflight-retry-accumulates")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "inflight-retry-accumulates" / "p2p"),
    )
    peer = _register_peer(node, "peer:3011")
    block_hash = b"\x22" * 32
    node._sync_inflight_max_retries = 2
    node._sync_last_inflight_reset_at = 0.0

    started_at = time.time() - 10
    node._sync_inflight_blocks[block_hash] = started_at
    node._sync_inflight_peers[block_hash] = peer.remote
    node._sync_inflight_block_requests[block_hash] = _SyncRequest(
        request_id="req-retry-1",
        peer_id=peer.remote,
        kind="blocks",
        started_at=started_at,
        deadline_at=started_at + 1,
        retry_count=0,
        item_hash=block_hash,
    )
    node._expire_inflight_blocks()
    assert node._sync_block_retry_counts.get(block_hash) == 1

    started_at = time.time() - 10
    node._sync_inflight_blocks[block_hash] = started_at
    node._sync_inflight_peers[block_hash] = peer.remote
    node._sync_inflight_block_requests[block_hash] = _SyncRequest(
        request_id="req-retry-2",
        peer_id=peer.remote,
        kind="blocks",
        started_at=started_at,
        deadline_at=started_at + 1,
        retry_count=1,
        item_hash=block_hash,
    )
    node._expire_inflight_blocks()

    assert block_hash not in node._sync_block_retry_counts
    assert node._stats["sync_inflight_reset"] >= 1


def test_peer_broadcast_score_prefers_broadcaster(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "broadcast-score")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "broadcast-score" / "p2p"),
    )
    peer_a = _register_peer(node, "peer:5001")
    peer_b = _register_peer(node, "peer:5002")

    peer_a.hello["head_height"] = 10
    peer_b.hello["head_height"] = 10
    node._update_peer_head_table(peer_a, height=10, source="test", head_hash=None)
    node._update_peer_head_table(peer_b, height=10, source="test", head_hash=None)

    now = time.time()
    peer_a.broadcast.last_inventory_at = now
    peer_a.broadcast.successful_headers_served = 2
    peer_b.broadcast.duplicate_header_batches = 3

    selected = node._select_sync_peer()
    assert selected == peer_a


def test_block_peer_selection_prefers_proven_near_tip_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps_sync, deps = _make_deps(tmp_path, "block-peer-near-tip")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "block-peer-near-tip" / "p2p"),
    )
    peer_max = _register_peer(node, "peer:5501")
    peer_good = _register_peer(node, "peer:5502")

    node._local_head = lambda: (100, None)  # type: ignore[method-assign]
    peer_max.hello["head_height"] = 105
    peer_good.hello["head_height"] = 103
    node._update_peer_head_table(peer_max, height=105, source="test", head_hash=None)
    node._update_peer_head_table(peer_good, height=103, source="test", head_hash=None)

    now = time.time()
    peer_good.broadcast.last_inventory_at = now
    peer_good.broadcast.last_head_advancement_at = now
    peer_good.broadcast.successful_headers_served = 2
    peer_good.broadcast.successful_blocks_served = 2

    monkeypatch.setattr("p2p.node.p2p_service.random.shuffle", lambda items: None)
    selected = node._select_block_peer(needed_height=101)
    assert selected == peer_good


def test_sync_stall_rotation_avoids_max_height_only_starvation(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "stall-near-tip")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "stall-near-tip" / "p2p"),
    )
    peer_stalled = _register_peer(node, "peer:5601")
    peer_productive = _register_peer(node, "peer:5602")

    node._local_head = lambda: (100, None)  # type: ignore[method-assign]
    node._sync_best_header = None
    node._sync_active_block_peer = peer_stalled.remote

    peer_stalled.hello["head_height"] = 105
    peer_productive.hello["head_height"] = 103
    node._update_peer_head_table(peer_stalled, height=105, source="test", head_hash=None)
    node._update_peer_head_table(peer_productive, height=103, source="test", head_hash=None)

    now = time.time()
    peer_stalled.last_block_request_at = now - 10.0
    peer_productive.broadcast.last_inventory_at = now
    peer_productive.broadcast.last_head_advancement_at = now
    peer_productive.broadcast.successful_headers_served = 3
    peer_productive.broadcast.successful_blocks_served = 1

    node._handle_sync_stall(reason=STALL_BLOCK_PEER_UNRESPONSIVE)

    assert node._sync_active_block_peer == peer_productive.remote


def test_headers_equal_blocks_stall_enqueues_header_retry(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "headers-equal-blocks-retry")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "headers-equal-blocks-retry" / "p2p"),
    )
    peer = _register_peer(node, "peer:5603")
    peer.hello["head_height"] = 10
    node._update_peer_head_table(peer, height=10, source="test", head_hash=None)
    node._local_head = lambda: (5, node._genesis_hash().hex())  # type: ignore[method-assign]
    node._sync_target_height = 10
    node._sync_best_header = None
    node._sync_last_header_error = "headers_empty"
    node._sync_last_progress_at = time.time() - node._sync_stall_timeout - 1.0

    node._handle_sync_stall(reason=STALL_BLOCK_PEER_UNRESPONSIVE)

    assert node._sync_header_retry_queue
    assert node._sync_block_stalled_reason is None
    assert node._sync_requested is True


def test_watchdog_resets_from_highest_next_height(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "reset-next-height")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "reset-next-height" / "p2p"),
    )
    peer_good = _register_peer(node, "peer:6001")
    peer_silent = _register_peer(node, "peer:6002")

    peer_good.hello["head_height"] = 5
    peer_silent.hello["head_height"] = 5
    node._update_peer_head_table(peer_good, height=5, source="test", head_hash=None)
    node._update_peer_head_table(peer_silent, height=5, source="test", head_hash=None)

    peer_good.broadcast.last_inventory_at = time.time()
    peer_good.broadcast.successful_headers_served = 1

    local_height, local_hash = node._local_head()
    node._sync_watchdog_last_height = int(local_height or 0)
    node._sync_watchdog_last_hash = local_hash
    node._sync_watchdog_timeout = 0.01
    node._sync_stall_timeout = 0.01
    node._sync_last_progress_at = time.time() - 1.0
    node._sync_watchdog_last_progress_at = time.time() - 1.0

    node._sync_watchdog_attempts = 1
    node._sync_watchdog_check(
        now=time.time(),
        head_height=int(local_height or 0),
        head_hash=local_hash,
    )

    assert node._sync_last_recovery_action == "reset_from_highest_next_height"
    assert node._sync_header_retry_queue
    request = node._sync_header_retry_queue[-1]
    assert request.start_height == int(local_height or 0) + 1
    assert request.peer_id == peer_good.remote


def test_watchdog_skip_reset_when_synced(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "watchdog-synced")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "watchdog-synced" / "p2p"),
    )
    peer = _register_peer(node, "peer:7001")
    local_height, local_hash = node._local_head()

    peer.hello["head_height"] = int(local_height or 0)
    node._update_peer_head_table(
        peer, height=int(local_height or 0), source="test", head_hash=None
    )

    node._sync_watchdog_last_height = int(local_height or 0)
    node._sync_watchdog_last_hash = local_hash
    node._sync_watchdog_timeout = 0.01
    node._sync_stall_timeout = 0.01
    node._sync_last_progress_at = time.time() - 1.0
    node._sync_watchdog_last_progress_at = time.time() - 1.0

    node._sync_watchdog_attempts = 1
    node._sync_watchdog_check(
        now=time.time(),
        head_height=int(local_height or 0),
        head_hash=local_hash,
    )

    assert node._sync_last_recovery_action != "reset_from_highest_next_height"


@pytest.mark.asyncio
async def test_inflight_header_expiry_requeues(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "inflight-header-expiry")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "inflight-header-expiry" / "p2p"),
    )
    peer = _register_peer(node, "peer:3001")
    started_at = time.monotonic() - 10
    request_id = "req-headers-1"
    peer.pending_header_request_id = request_id
    peer.pending_headers = asyncio.get_event_loop().create_future()
    node._sync_inflight_header_requests[(peer.remote, request_id)] = _SyncRequest(
        request_id=request_id,
        peer_id=peer.remote,
        kind="headers",
        started_at=started_at,
        deadline_at=started_at - 1,
        retry_count=0,
        start_height=1,
        count=10,
        locator=[node._genesis_hash()],
        locator_mode="default",
        anchor_height=0,
        anchor_hash=node._genesis_hash(),
    )
    node._sync_inflight_headers = 1

    node._expire_inflight_headers()

    assert node._sync_header_retry_queue
    assert node._sync_requested
    assert node._stats["headers_req_timeout"] == 1


def test_not_anchored_recovery_sets_probe(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "not-anchored-recovery")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "not-anchored-recovery" / "p2p"),
    )
    peer = _register_peer(node, "peer:3002")
    parent_hash = b"\x02" * 32
    header = node._header_from_compact(
        HeaderCompact(
            hash=b"\x03" * 32,
            height=1,
            parent=parent_hash,
            theta_micro=0,
            timestamp=1,
        )
    )

    node._sync_requested = False
    node._note_not_anchored(
        peer,
        header=header,
        anchor_height=0,
        anchor_hash=None,
        reason="parent_unknown",
        allow_probe=True,
    )

    assert node._sync_last_header_error == "not_anchored"
    assert node._sync_anchor_probe_hash == parent_hash
    assert node._sync_requested


def test_best_peer_head_ignores_stale_and_cooldown(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "best-peer-head")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "best-peer-head" / "p2p"),
    )
    peer_stale = _register_peer(node, "peer:4001")
    peer_cooldown = _register_peer(node, "peer:4002")
    peer_fresh = _register_peer(node, "peer:4003")

    node._sync_peer_head_stale_sec = 0.01
    node._update_peer_head_table(peer_stale, height=100, source="test", head_hash=None)
    node._update_peer_head_table(
        peer_cooldown, height=90, source="test", head_hash=None
    )
    node._update_peer_head_table(peer_fresh, height=80, source="test", head_hash=None)

    now = time.time()
    node._sync_peer_heads[peer_stale.remote].updated_at = now - 1.0
    node._sync_peer_heads[peer_cooldown.remote].cooldown_until = now + 60.0

    best_peer, best_height, _best_hash = node._best_peer_head()
    assert best_peer == peer_fresh
    assert best_height == 80


def test_idle_while_behind_triggers_kick_and_requeue(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "idle-while-behind")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "idle-while-behind" / "p2p"),
    )
    peer = _register_peer(node, "peer:4004")

    node._sync_requested = False
    node._enforce_sync_invariants(
        now=time.time(),
        best_block_height=0,
        best_header_height=0,
        target_height=10,
        best_peer=peer,
    )

    assert node._sync_requested
    assert node._sync_header_retry_queue


@pytest.mark.asyncio
async def test_watchdog_recovers_when_blocks_stop(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "watchdog-recovery")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "watchdog-recovery" / "p2p"),
    )
    peer = _register_peer(node, "peer:3003")
    peer.anchored = True
    node._sync_best_header = node._header_from_compact(
        HeaderCompact(
            hash=b"\x04" * 32,
            height=10,
            parent=b"\x00" * 32,
            theta_micro=0,
            timestamp=1,
        )
    )
    block_hash = b"\x05" * 32
    node._sync_block_queue.append(block_hash)
    node._sync_block_queue_set.add(block_hash)
    node._sync_watchdog_timeout = 1.0
    node._sync_watchdog_last_progress_at = time.time() - 1

    async def _noop_sync_once(*_args, **_kwargs):
        return {}

    async def _noop_schedule(*_args, **_kwargs):
        return 0

    node._sync_once = _noop_sync_once  # type: ignore[assignment]
    node._schedule_block_requests = _noop_schedule  # type: ignore[assignment]

    node._sync_tick_sec = 0.05
    node._running = True
    task = asyncio.create_task(node._sync_loop())
    try:
        await asyncio.sleep(0.15)
        assert node._sync_last_recovery_action in {
            "retry_blocks_new_peer",
            "watchdog_requeue",
        }
        assert node._sync_last_recovery_at > 0
    finally:
        node._running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_watchdog_recovery_waits_for_timeout_window(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "watchdog-rate-limit")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "watchdog-rate-limit" / "p2p"),
    )
    _register_peer(node, "peer:3004")

    now = time.time()
    node._sync_watchdog_timeout = 1.0
    node._sync_watchdog_last_height = 0
    node._sync_watchdog_last_hash = node._genesis_hash().hex()
    node._sync_watchdog_last_progress_at = now - 10.0

    node._sync_watchdog_check(now=now, head_height=0, head_hash=node._genesis_hash().hex())
    assert node._sync_watchdog_attempts == 1
    assert node._sync_last_recovery_action == "watchdog_requeue"

    node._sync_watchdog_check(
        now=now + 0.1,
        head_height=0,
        head_hash=node._genesis_hash().hex(),
    )
    assert node._sync_watchdog_attempts == 1
    assert node._sync_last_recovery_action == "watchdog_requeue"


@pytest.mark.asyncio
async def test_no_false_stalled_on_at_tip(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "no-false-stalled")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "no-false-stalled" / "p2p"),
    )
    _register_peer(node, "peer:1003")

    async def _fake_fetch_headers(_peer: _PeerState):
        return []

    node._fetch_headers = _fake_fetch_headers  # type: ignore[assignment]
    node._empty_headers_reason = lambda *_args, **_kwargs: "at_tip"  # type: ignore[assignment]

    await node._sync_once(force=True)
    snapshot = node.sync_status_snapshot()
    assert snapshot.stall_reason is None
    assert snapshot.phase != "STALLED"


@pytest.mark.asyncio
async def test_headers_empty_at_tip_transitions_idle(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "headers-empty-tip")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "headers-empty-tip" / "p2p"),
    )
    _register_peer(node, "peer:1005")

    async def _fake_fetch_headers(_peer: _PeerState):
        return []

    node._fetch_headers = _fake_fetch_headers  # type: ignore[assignment]
    node._empty_headers_reason = (  # type: ignore[assignment]
        lambda *_args, **_kwargs: "headers_empty"
    )
    node._local_head = lambda: (1, node._genesis_hash().hex())

    await node._sync_once(force=True)
    snapshot = node.sync_status_snapshot()
    assert snapshot.phase in {"SYNCED", "IDLE"}
    assert snapshot.in_flight_headers == 0


@pytest.mark.asyncio
async def test_headers_empty_rotates_peer(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "headers-empty-rotate")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "headers-empty-rotate" / "p2p"),
    )
    peer_a = _register_peer(node, "peer:1006")
    peer_b = _register_peer(node, "peer:1007")
    peer_a.hello["head_height"] = 2
    peer_b.hello["head_height"] = 2

    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    child_hash = compute_header_hash(child_block.header)
    parent_hash = genesis.hash()

    headers = [
        HeaderCompact(
            hash=child_hash,
            height=1,
            parent=parent_hash,
            theta_micro=int(getattr(child_block.header, "thetaMicro", 0)),
            timestamp=int(getattr(child_block.header, "timestamp", 0)),
        )
    ]

    async def _fake_fetch_headers(peer: _PeerState):
        return [] if peer.remote == peer_a.remote else headers

    node._fetch_headers = _fake_fetch_headers  # type: ignore[assignment]
    node._empty_headers_reason = (  # type: ignore[assignment]
        lambda *_args, **_kwargs: "headers_empty"
    )

    await node._sync_once(force=True)
    assert node._sync_best_header is not None
    info = node._sync_peer_heads.get(peer_a.remote)
    assert info is not None
    assert info.cooldown_until > time.time()
    assert peer_a.header_cooldown_until > time.time()
    _eligible, ineligible = node._eligible_sync_peers()
    assert ineligible.get(peer_a.remote) == "headers_cooldown"


@pytest.mark.asyncio
async def test_sync_once_clears_transient_header_cooldown_while_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps_sync, deps = _make_deps(tmp_path, "header-cooldown-recovery")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "header-cooldown-recovery" / "p2p"),
    )
    peer = _register_peer(node, "peer:1008")
    peer.hello["head_height"] = 1
    node._update_peer_head_table(peer, height=1, source="test", head_hash=None)
    node._set_sync_backoff(peer, reason="headers_empty", delay=60.0)
    node._mark_peer_head_issue(peer, reason="headers_empty", cooldown=60.0)
    peer.header_cooldown_until = time.time() + 60.0
    peer.empty_header_responses = 1

    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    headers = [
        HeaderCompact(
            hash=compute_header_hash(child_block.header),
            height=1,
            parent=genesis.hash(),
            theta_micro=int(getattr(child_block.header, "thetaMicro", 0)),
            timestamp=int(getattr(child_block.header, "timestamp", 0)),
        )
    ]

    async def _fake_fetch_headers(_peer: _PeerState):
        return headers

    monkeypatch.setattr(node, "_fetch_headers", _fake_fetch_headers)

    await node._sync_once(force=True)

    assert node._sync_best_header is not None
    assert node._sync_best_header.height == 1
    assert peer.header_cooldown_until == 0.0
    assert peer.empty_header_responses == 0


@pytest.mark.asyncio
async def test_block_request_scheduling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps_sync, deps = _make_deps(tmp_path, "block-request-scheduling")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "block-request-scheduling" / "p2p"),
    )
    peer = _register_peer(node, "peer:1004")

    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    child_hash = compute_header_hash(child_block.header)
    node._sync_headers[child_hash] = node._sync_header_from_db(child_block.header)
    node._sync_best_header = node._sync_headers[child_hash]
    node._enqueue_missing_blocks([node._sync_headers[child_hash]])

    sent = []

    async def _fake_send(_peer: _PeerState, _msg_id, _payload) -> None:
        sent.append(_msg_id)

    monkeypatch.setattr(node, "_send", _fake_send)
    requested = await node._schedule_block_requests(peer)
    assert requested > 0
    assert node._sync_active_block_peer == peer.remote
    assert node._sync_inflight_blocks


@pytest.mark.asyncio
async def test_block_requests_wait_for_anchor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps_sync, deps = _make_deps(tmp_path, "block-wait-anchor")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "block-wait-anchor" / "p2p"),
    )
    peer = _register_peer(node, "peer:1300")
    peer.anchored = False

    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    child_hash = compute_header_hash(child_block.header)
    node._sync_headers[child_hash] = node._sync_header_from_db(child_block.header)
    node._sync_best_header = node._sync_headers[child_hash]
    node._enqueue_missing_blocks([node._sync_headers[child_hash]])

    sent = []

    async def _fake_send(_peer: _PeerState, _msg_id, _payload) -> None:
        sent.append(_msg_id)

    monkeypatch.setattr(node, "_send", _fake_send)
    node._should_enforce_checkpoint_anchor = lambda: True  # type: ignore[assignment]

    requested = await node._schedule_block_requests(peer)
    assert requested == 0
    assert not sent


@pytest.mark.asyncio
async def test_block_requests_skip_zero_height_peer(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "block-skip-zero-height")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "block-skip-zero-height" / "p2p"),
    )
    peer = _register_peer(node, "peer:1100")
    peer.hello["head_height"] = 0
    node._sync_peer_heads.pop(peer.remote, None)

    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    child_hash = compute_header_hash(child_block.header)
    node._sync_headers[child_hash] = node._sync_header_from_db(child_block.header)
    node._sync_best_header = node._sync_headers[child_hash]
    node._enqueue_missing_blocks([node._sync_headers[child_hash]])

    requested = await node._schedule_block_requests()
    assert requested == 0
    assert node._sync_inflight_blocks == {}


@pytest.mark.asyncio
async def test_block_requests_prefer_height_peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps_sync, deps = _make_deps(tmp_path, "block-prefer-height")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "block-prefer-height" / "p2p"),
    )
    low_peer = _register_peer(node, "peer:1200")
    high_peer = _register_peer(node, "peer:1201")
    low_peer.hello["head_height"] = 0
    high_peer.hello["head_height"] = 500
    node._update_peer_head_table(low_peer, height=0, source="test", head_hash=None)
    node._update_peer_head_table(high_peer, height=500, source="test", head_hash=None)

    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    child_hash = compute_header_hash(child_block.header)
    node._sync_headers[child_hash] = node._sync_header_from_db(child_block.header)
    node._sync_best_header = node._sync_headers[child_hash]
    node._enqueue_missing_blocks([node._sync_headers[child_hash]])

    sent_to: list[str] = []

    async def _fake_send(peer: _PeerState, _msg_id, _payload) -> None:
        sent_to.append(peer.remote)

    monkeypatch.setattr(node, "_send", _fake_send)
    requested = await node._schedule_block_requests()
    assert requested > 0
    assert node._sync_active_block_peer == high_peer.remote
    assert sent_to and all(remote == high_peer.remote for remote in sent_to)


@pytest.mark.asyncio
async def test_cache_miss_requests_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deps_sync, deps = _make_deps(tmp_path, "cache-miss-requests")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "cache-miss-requests" / "p2p"),
    )
    peer = _register_peer(node, "peer:1300")
    peer.hello["head_height"] = 10

    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    child_hash = compute_header_hash(child_block.header)

    node._sync_cache = _NullCache()
    node._sync_block_queue.append(child_hash)
    node._sync_block_queue_set.add(child_hash)

    sent = []

    async def _fake_send(_peer: _PeerState, _msg_id, _payload) -> None:
        sent.append(_msg_id)

    monkeypatch.setattr(node, "_send", _fake_send)
    requested = await node._schedule_block_requests()
    assert requested > 0
    assert node._sync_inflight_blocks


@pytest.mark.asyncio
async def test_cache_failure_does_not_set_peer(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "cache-failure-no-peer")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "cache-failure-no-peer" / "p2p"),
    )
    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    child_hash = compute_header_hash(child_block.header)

    node._sync_cache = _BadCache(b"bad-block-bytes")

    async def _fake_import(_payload, origin_remote=None):
        return False, "invalid_block"

    node._import_block_payload = _fake_import  # type: ignore[assignment]

    ok = await node._try_import_cached_block(child_hash)
    assert not ok
    assert node._sync_last_block_error == STALL_CACHE_SHORT_CIRCUIT
    assert node._sync_last_block_error_peer is None


@pytest.mark.asyncio
async def test_drop_peer_requeues_blocks_and_clears_target_tip(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "drop-peer-requeue")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "drop-peer-requeue" / "p2p"),
    )
    peer = _register_peer(node, "peer:1399")
    peer.peer_id = "peer-1399"
    target_hash = b"\x44" * 32
    peer.hello["head_height"] = 25
    peer.hello["head_hash"] = target_hash
    node._update_peer_head_table(peer, height=25, source="test", head_hash=target_hash)
    block_hash = b"\x55" * 32
    started_at = time.time()
    node._sync_inflight_blocks[block_hash] = started_at
    node._sync_inflight_peers[block_hash] = peer.remote
    node._sync_inflight_block_requests[block_hash] = _SyncRequest(
        request_id="drop-peer-block",
        peer_id=peer.remote,
        kind="blocks",
        started_at=started_at,
        deadline_at=started_at + 30.0,
        retry_count=0,
        item_hash=block_hash,
    )
    node._sync_active_header_peer = peer.remote
    node._sync_active_block_peer = peer.remote

    assert node._update_sync_target_tip(time.time()) is not None

    await node._drop_peer(peer, reason="test_disconnect")

    assert block_hash in node._sync_block_queue_set
    assert block_hash not in node._sync_inflight_blocks
    assert peer.remote not in node._sync_peer_heads
    assert node._sync_active_header_peer is None
    assert node._sync_active_block_peer is None
    assert node._sync_target_tip is None
    assert node._sync_last_block_error == STALL_BLOCK_PEER_UNRESPONSIVE


@pytest.mark.asyncio
async def test_block_stall_recovery_rotates_peer(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "block-stall-recovery")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "block-stall-recovery" / "p2p"),
    )
    peer_a = _register_peer(node, "peer:1400")
    peer_b = _register_peer(node, "peer:1401")
    peer_a.hello["head_height"] = 5
    peer_b.hello["head_height"] = 10
    node._update_peer_head_table(peer_a, height=5, source="test", head_hash=None)
    node._update_peer_head_table(peer_b, height=10, source="test", head_hash=None)

    genesis = deps_sync.header_by_number(0)
    assert genesis is not None
    child_block = _make_child_block(genesis)
    child_hash = compute_header_hash(child_block.header)
    node._sync_headers[child_hash] = node._sync_header_from_db(child_block.header)
    node._sync_best_header = node._sync_headers[child_hash]
    node._sync_block_queue.append(child_hash)
    node._sync_block_queue_set.add(child_hash)

    node._sync_active_block_peer = peer_a.remote
    node._sync_stall_timeout = 0.01
    node._sync_last_progress_at = node._sync_last_progress_at - 1.0

    node._maybe_mark_block_stalled(time.time())
    assert node._sync_block_stalled_reason == STALL_BLOCK_TIMEOUT

    node._handle_sync_stall(reason=STALL_BLOCK_TIMEOUT)
    assert node._sync_last_recovery_action == "retry_blocks_new_peer"
    assert node._sync_active_block_peer == peer_b.remote


def test_sync_stall_clears_transient_block_penalties(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "stall-clear-transient-block-penalties")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "stall-clear-transient-block-penalties" / "p2p"),
    )
    peer_a = _register_peer(node, "peer:5701")
    peer_b = _register_peer(node, "peer:5702")
    now = time.time()
    for peer in (peer_a, peer_b):
        peer.hello["head_height"] = 5
        node._update_peer_head_table(peer, height=5, source="test", head_hash=None)
        node._set_block_backoff(peer, reason="block_timeout", delay=60.0)
        peer.block_failures = 2
        peer.block_cooldown_until = now + 60.0

    block_hash = b"\x12" * 32
    node._sync_block_queue.append(block_hash)
    node._sync_block_queue_set.add(block_hash)
    node._sync_block_queue_heights[block_hash] = 2
    node._sync_last_block_error = STALL_BLOCK_TIMEOUT
    node._sync_active_block_peer = peer_a.remote
    node._create_child_task = lambda coro, *_args, **_kwargs: coro.close()  # type: ignore[assignment]

    node._handle_sync_stall(reason=STALL_BLOCK_TIMEOUT)

    assert node._sync_last_recovery_action == "retry_blocks_new_peer"
    assert node._sync_active_block_peer == peer_b.remote
    for peer in (peer_a, peer_b):
        assert node._peer_backoff_key(peer) not in node._sync_block_peer_backoff
        assert peer.block_cooldown_until == 0.0
        assert peer.block_failures == 0


def test_select_sync_peer_considers_proven_peer_when_heights_are_close(
    tmp_path: Path,
) -> None:
    deps_sync, deps = _make_deps(tmp_path, "sync-peer-selection")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "sync-peer-selection" / "p2p"),
    )
    noisy = _register_peer(node, "10.1.0.2:30333")
    healthy = _register_peer(node, "10.1.0.3:30333")
    noisy.peer_id = "peer-noisy"
    healthy.peer_id = "peer-healthy"
    noisy.hello["head_height"] = 30
    healthy.hello["head_height"] = 28
    healthy.broadcast.successful_headers_served = 3
    healthy.anchored = True

    selected_remotes: set[str] = set()
    for _ in range(20):
        selected = node._select_sync_peer()
        assert selected is not None
        selected_remotes.add(selected.remote)
    assert healthy.remote in selected_remotes
