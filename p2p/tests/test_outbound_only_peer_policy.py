from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from p2p.node.p2p_service import P2PService, PeerMisbehavior, _PeerState
from p2p.tests import free_port, tcp_multiaddr
from p2p.tests.test_sync_loop_behavior import _make_deps
from p2p.wire.encoding import encode_payload
from p2p.wire.messages import Blocks, Hello


def _register_peer(node: P2PService, peer_addr: str) -> _PeerState:
    session = node._peer_registry.register(peer_addr, "outbound")
    peer = _PeerState(
        session_id=session.session_id,
        remote=peer_addr,
        direction="outbound",
        conn=None,
        stream=None,
        framer=None,
        write_lock=asyncio.Lock(),
    )
    node._peers[(peer_addr, "outbound")] = peer
    return peer


@pytest.mark.asyncio
async def test_handshake_rejects_outbound_only_peer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANIMICA_P2P_ENFORCE_INBOUND_REACHABILITY", "true")
    monkeypatch.setenv("ANIMICA_P2P_OUTBOUND_ONLY_BAN_TTL", "60")

    deps_sync, deps = _make_deps(tmp_path, "outbound-only-reject")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "outbound-only-reject" / "p2p"),
    )
    peer = _register_peer(node, "198.51.100.10:30333")

    async def _noop_send(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(node, "_send", _noop_send)

    hello = Hello(
        version="2",
        agent="animica-p2p/test",
        chain_id=node.chain_id,
        listen_port=0,
        listen_addrs=[],
        genesis_hash=node._genesis_header_hash(),
        genesis_header_hash=node._genesis_header_hash(),
        genesis_block_hash=node._genesis_block_hash(),
        fork_id=node._fork_id(),
        consensus_id=node._consensus_id(),
        protocol_version=node._protocol_version(),
        genesis_identity=node._genesis_identity(),
        network_params_hash=node._network_params_hash(),
        peer_id=b"\x44" * 32,
        head_height=1,
        head_hash=node._genesis_header_hash(),
        capabilities=["sync"],
        timestamp=0,
    )

    with pytest.raises(PeerMisbehavior) as exc:
        await node._handle_hello(peer, encode_payload(hello))

    assert exc.value.reason == "outbound_only_no_inbound"
    assert node._is_outbound_only_blocklisted(
        peer_id=peer.peer_id, remote=peer.remote
    )


@pytest.mark.asyncio
async def test_handshake_accepts_peer_with_inbound_advertisement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANIMICA_P2P_ENFORCE_INBOUND_REACHABILITY", "true")

    deps_sync, deps = _make_deps(tmp_path, "outbound-only-accept")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "outbound-only-accept" / "p2p"),
    )
    peer = _register_peer(node, "198.51.100.11:40444")

    async def _noop_send(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(node, "_send", _noop_send)

    hello = Hello(
        version="2",
        agent="animica-p2p/test",
        chain_id=node.chain_id,
        listen_port=30333,
        listen_addrs=[],
        genesis_hash=node._genesis_header_hash(),
        genesis_header_hash=node._genesis_header_hash(),
        genesis_block_hash=node._genesis_block_hash(),
        fork_id=node._fork_id(),
        consensus_id=node._consensus_id(),
        protocol_version=node._protocol_version(),
        genesis_identity=node._genesis_identity(),
        network_params_hash=node._network_params_hash(),
        peer_id=b"\x55" * 32,
        head_height=1,
        head_hash=node._genesis_header_hash(),
        capabilities=["sync"],
        timestamp=0,
    )

    await node._handle_hello(peer, encode_payload(hello))

    assert peer.hello_done.is_set()
    assert peer.accepts_inbound is True


@pytest.mark.asyncio
async def test_blocks_forfeited_from_outbound_only_peer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANIMICA_P2P_ENFORCE_INBOUND_REACHABILITY", "true")
    monkeypatch.setenv("ANIMICA_P2P_FORFEIT_OUTBOUND_ONLY_BLOCKS", "true")
    monkeypatch.setenv("ANIMICA_P2P_OUTBOUND_ONLY_BAN_TTL", "60")

    deps_sync, deps = _make_deps(tmp_path, "outbound-only-forfeit")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "outbound-only-forfeit" / "p2p"),
    )
    peer = _register_peer(node, "198.51.100.12:50505")
    peer.peer_id = "66" * 32
    peer.accepts_inbound = False

    scheduled: list[str] = []

    def _capture_task(coro, *, name: str | None = None):
        if asyncio.iscoroutine(coro):
            coro.close()
        if name:
            scheduled.append(name)

    monkeypatch.setattr(node, "_create_child_task", _capture_task)

    await node._handle_blocks(peer, encode_payload(Blocks(blocks=[b"not_a_block"])))

    assert node._stats["blocks_rejected"] >= 1
    assert peer.block_failures >= 1
    assert any(name.startswith("p2p.drop_peer@") for name in scheduled)
    assert node._is_outbound_only_blocklisted(
        peer_id=peer.peer_id, remote=peer.remote
    )


def test_sync_peer_eligibility_rejects_outbound_only_peer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANIMICA_P2P_ENFORCE_INBOUND_REACHABILITY", "true")

    deps_sync, deps = _make_deps(tmp_path, "outbound-only-eligibility")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "outbound-only-eligibility" / "p2p"),
    )
    peer = _register_peer(node, "198.51.100.13:30333")
    peer.peer_id = "77" * 32
    peer.hello_done.set()
    peer.ready_for_sync = True
    peer.accepts_inbound = False
    peer.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
        "head_height": 10,
        "head_hash": b"\x01" * 32,
    }

    ok, reason = node._sync_peer_eligibility(peer)
    assert ok is False
    assert reason == "outbound_only_no_inbound"


@pytest.mark.asyncio
async def test_block_announce_forfeited_from_outbound_only_peer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANIMICA_P2P_ENFORCE_INBOUND_REACHABILITY", "true")
    monkeypatch.setenv("ANIMICA_P2P_FORFEIT_OUTBOUND_ONLY_BLOCKS", "true")
    monkeypatch.setenv("ANIMICA_P2P_OUTBOUND_ONLY_BAN_TTL", "60")

    deps_sync, deps = _make_deps(tmp_path, "outbound-only-announce-forfeit")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "outbound-only-announce-forfeit" / "p2p"),
    )
    peer = _register_peer(node, "198.51.100.14:40444")
    peer.peer_id = "88" * 32
    peer.accepts_inbound = False

    scheduled: list[str] = []

    def _capture_task(coro, *, name: str | None = None):
        if asyncio.iscoroutine(coro):
            coro.close()
        if name:
            scheduled.append(name)

    monkeypatch.setattr(node, "_create_child_task", _capture_task)

    await node._handle_block_announce(peer, b"")

    assert any(name.startswith("p2p.drop_peer@") for name in scheduled)
    assert node._is_outbound_only_blocklisted(
        peer_id=peer.peer_id, remote=peer.remote
    )
