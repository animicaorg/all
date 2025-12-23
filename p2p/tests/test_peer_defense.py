from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState
from p2p.tests import tcp_multiaddr

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


class _DummyConn:
    def __init__(self, remote: str) -> None:
        self.info = types.SimpleNamespace(remote_addr=remote)

    async def open_stream(self) -> object:
        return object()

    async def close(self) -> None:
        return None


def _make_deps(tmp_path: Path, name: str) -> P2PDeps:
    db_path = tmp_path / f"{name}.db"
    return P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))


def _make_service(tmp_path: Path, name: str) -> P2PService:
    deps_sync = _make_deps(tmp_path, name)
    return P2PService(
        listen_addrs=[tcp_multiaddr(0)],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps_sync,
        peerstore_path=str(tmp_path / name / "p2p"),
    )


def _register_peer(node: P2PService, remote: str, direction: str = "inbound") -> _PeerState:
    session = node._peer_registry.register(remote, direction)
    peer = _PeerState(
        session_id=session.session_id,
        remote=remote,
        direction=direction,
        conn=_DummyConn(remote),
        stream=None,
        framer=None,
        write_lock=asyncio.Lock(),
        netgroup=node._netgroup_key(remote),
    )
    node._peers[remote] = peer
    node._peers_by_session[peer.session_id] = peer
    return peer


@pytest.mark.asyncio
async def test_score_decay_and_banlist(tmp_path: Path) -> None:
    node = _make_service(tmp_path, "scores")
    peer = _register_peer(node, "203.0.113.10:30333")
    node._ban_thresholds = [(10, 5.0)]

    node.penalize_peer(peer, "invalid_header", 5)
    assert peer.misbehavior_score == 5
    assert not node._is_banned(peer.remote)

    node.penalize_peer(peer, "invalid_header", 5)
    await asyncio.sleep(0)
    assert node._is_banned(peer.remote)

    node._misbehavior_decay_points = 3
    peer2 = _register_peer(node, "203.0.113.11:30333")
    peer2.misbehavior_score = 9
    node.decay_scores()
    assert peer2.misbehavior_score == 6


def test_netgroup_calculation(tmp_path: Path) -> None:
    node = _make_service(tmp_path, "netgroup")
    assert node._netgroup_key("192.168.1.12:30333") == "192.168.0.0/16"
    assert node._netgroup_key("[2001:db8::1]:30333").startswith("2001:db8::/48")


@pytest.mark.asyncio
async def test_netgroup_limits_reject_connections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node = _make_service(tmp_path, "netgroup-limit")
    node._max_inbound_per_netgroup = 1
    node._create_child_task = lambda coro, **_kwargs: coro.close()

    await node._register_conn(_DummyConn("198.51.100.1:30333"), direction="inbound")
    assert len(node._peers) == 1

    await node._register_conn(_DummyConn("198.51.100.2:30333"), direction="inbound")
    assert len(node._peers) == 1


def test_sync_stall_rotation(tmp_path: Path) -> None:
    node = _make_service(tmp_path, "stall")
    node._sync_best_header = types.SimpleNamespace(height=10)
    node._sync_last_block_at = 1.0
    node._sync_last_header_at = 10.0
    node._sync_block_stalled_reason = "blocks stalled"

    peer_a = _register_peer(node, "203.0.113.1:30333", direction="outbound")
    peer_b = _register_peer(node, "203.0.113.2:30333", direction="outbound")
    peer_a.peer_id = "peer-a"
    peer_b.peer_id = "peer-b"
    peer_a.hello = {"head_height": 10}
    peer_b.hello = {"head_height": 11}
    node._sync_active_block_peer = peer_a.remote

    node._handle_sync_stall(reason="blocks stalled")
    assert node._sync_active_block_peer == peer_b.remote


def test_missing_parent_escalation(tmp_path: Path) -> None:
    node = _make_service(tmp_path, "missing-parent")
    node._missing_parent_threshold = 1
    node._sync_peer_penalty_threshold = 99
    peer = _register_peer(node, "203.0.113.55:30333", direction="outbound")
    peer.peer_id = "peer-c"
    peer.hello = {"head_height": 2}

    sync_block = types.SimpleNamespace(hash=b"x" * 32, parent_hash=b"y" * 32)
    node._handle_missing_parent(peer, sync_block)
    assert node._sync_block_stalled_reason == "missing parent"
