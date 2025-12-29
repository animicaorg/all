from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from core.chain.block_import import _theta_to_target, compute_header_hash
from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState
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
