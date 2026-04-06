from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState
from p2p.tests import free_port, tcp_multiaddr

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_node(tmp_path: Path, name: str) -> P2PService:
    db_path = tmp_path / f"{name}.db"
    deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / name / "p2p"),
    )


def _register_peer(node: P2PService, remote: str, *, head_height: int) -> _PeerState:
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
        "head_height": head_height,
        "head_hash": node._genesis_hash(),
    }
    node._peers[remote] = peer
    node._peers_by_session[peer.session_id] = peer
    node._update_peer_head_table(peer, height=head_height, source="test", head_hash=None)
    return peer


def test_snapshot_target_uses_network_best(tmp_path: Path) -> None:
    node = _make_node(tmp_path, "snapshot-target-height")
    _register_peer(node, "peer:9201", head_height=3)
    _register_peer(node, "peer:9202", head_height=7)
    node._sync_target_height = 2

    snapshot = node.sync_status_snapshot(refresh=True)

    assert snapshot.network_best_height == 7
    assert snapshot.target_height == 7


def test_next_block_needed_reports_height_during_catchup(tmp_path: Path) -> None:
    node = _make_node(tmp_path, "next-block-needed")
    node._local_head = lambda: (1, None)  # type: ignore[method-assign]
    node._sync_target_height = 3

    needed_height, needed_hash = node._next_block_needed()

    assert needed_height == 2
    assert needed_hash is None


@pytest.mark.asyncio
async def test_sync_once_does_not_stop_on_stale_target(tmp_path: Path) -> None:
    node = _make_node(tmp_path, "stale-target")
    peer = _register_peer(node, "peer:9301", head_height=3)
    node._sync_target_height = 1
    node._local_head = lambda: (1, None)  # type: ignore[method-assign]
    node._eligible_sync_peers = lambda *args, **kwargs: ([peer], {})  # type: ignore[method-assign]
    node._select_sync_peer = lambda *args, **kwargs: None  # type: ignore[method-assign]

    await node._sync_once(force=False)

    assert node._sync_phase != "TARGET_REACHED"


def test_sync_status_refresh_reflects_new_head(tmp_path: Path) -> None:
    node = _make_node(tmp_path, "status-refresh-head")
    node._canonical_head_for_status = lambda: (1, "0x01")  # type: ignore[method-assign]
    snap1 = node.sync_status_snapshot(refresh=True)
    assert snap1.head_height == 1

    node._canonical_head_for_status = lambda: (3, "0x03")  # type: ignore[method-assign]
    snap2 = node.sync_status_snapshot(refresh=True)
    assert snap2.head_height == 3
