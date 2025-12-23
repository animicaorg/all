from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState, _SyncHeader
from p2p.tests import tcp_multiaddr
from p2p.wire.encoding import encode_payload
from p2p.wire.frames import Framer
from p2p.wire.messages import Blocks, HeaderCompact, Hello

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_deps(tmp_path: Path, name: str) -> P2PDeps:
    db_path = tmp_path / f"{name}.db"
    return P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))


def _make_child_block(sync_deps: P2PDeps) -> Block:
    height, head_hash = sync_deps.head()
    header = sync_deps.header_by_hash(head_hash) if head_hash else None
    if header is None:
        header = sync_deps.header_by_number(0)
    assert header is not None

    timestamp = int(getattr(header, "timestamp", 0)) + 1
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
    return Block(header=child, txs=(), proofs=(), receipts=None)


def _make_peer() -> _PeerState:
    peer = _PeerState(
        session_id="peer-1",
        remote="peer-1:0",
        direction="inbound",
        conn=None,
        stream=None,
        framer=None,
        write_lock=asyncio.Lock(),
    )
    peer.hello = {}
    return peer


def _register_peer(node: P2PService, remote: str) -> _PeerState:
    session = node._peer_registry.register(remote, "inbound")
    peer = _PeerState(
        session_id=session.session_id,
        remote=remote,
        direction="inbound",
        conn=None,
        stream=None,
        framer=Framer(aead=None),
        write_lock=asyncio.Lock(),
    )
    node._peers[remote] = peer
    node._peers_by_session[peer.session_id] = peer
    return peer


def _make_service(tmp_path: Path, name: str) -> tuple[P2PService, P2PDeps]:
    deps_sync = _make_deps(tmp_path, name)
    node = P2PService(
        listen_addrs=[tcp_multiaddr(0)],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps_sync,
        peerstore_path=str(tmp_path / name / "p2p"),
    )
    return node, deps_sync


def test_phase_not_idle_when_headers_ahead(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "phase")
    genesis = deps_sync.header_by_number(0)
    assert genesis is not None

    header = _SyncHeader(
        hash=b"\x01" * 32,
        parent_hash=genesis.hash(),
        height=1,
        theta_micro=0,
        timestamp=int(getattr(genesis, "timestamp", 0)) + 1,
    )
    node._sync_headers[header.hash] = header
    node._sync_best_header = header

    snap = node.sync_status_snapshot()
    assert snap.phase != "IDLE"


def test_header_advancement_enqueues_blocks(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "enqueue")
    block = _make_child_block(deps_sync)
    header = HeaderCompact(
        hash=block.header.hash(),
        height=int(block.header.height),
        parent=bytes(block.header.parentHash),
        theta_micro=int(getattr(block.header, "thetaMicro", 0)),
        timestamp=int(getattr(block.header, "timestamp", 0)),
    )

    peer = _make_peer()
    node._process_headers(peer, [header])

    assert node._queued_blocks_count() == 1


def test_sync_status_head_hash_matches_chain_head(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "head-hash")
    height, header = deps_sync.head()
    assert header is not None

    expected_hash = node._header_hash_for_status(header)
    snap = node.sync_status_snapshot()

    assert snap.head_height == height
    assert snap.head_hash == expected_hash
    assert snap.best_block_hash == expected_hash


@pytest.mark.asyncio
async def test_mocked_peer_headers_and_blocks_advance_head(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "mocked")
    block = _make_child_block(deps_sync)
    header = HeaderCompact(
        hash=block.header.hash(),
        height=int(block.header.height),
        parent=bytes(block.header.parentHash),
        theta_micro=int(getattr(block.header, "thetaMicro", 0)),
        timestamp=int(getattr(block.header, "timestamp", 0)),
    )

    peer = _make_peer()
    node._process_headers(peer, [header])

    payload = encode_payload(Blocks(blocks=[block.to_cbor()]))
    await node._handle_blocks(peer, payload)

    height, _ = deps_sync.head()
    assert height >= 1


@pytest.mark.asyncio
async def test_peer_ready_triggers_header_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node, _deps_sync = _make_service(tmp_path, "ready-trigger")
    peer = _register_peer(node, "203.0.113.10:30333")

    async def _noop_send(*_args, **_kwargs) -> None:
        return None

    def _noop_task(coro, **_kwargs):
        if asyncio.iscoroutine(coro):
            coro.close()

    monkeypatch.setattr(node, "_send", _noop_send)
    monkeypatch.setattr(node, "_create_child_task", _noop_task)

    hello = Hello(
        chain_id=node.chain_id,
        listen_port=30333,
        peer_id=b"\x11" * 32,
        head_hash=b"\x00" * 32,
        head_height=10,
    )
    await node._handle_hello(peer, encode_payload(hello))

    called = False

    async def _fake_fetch_headers(_peer: _PeerState):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(node, "_fetch_headers", _fake_fetch_headers)

    await node._sync_once()
    assert called is True
