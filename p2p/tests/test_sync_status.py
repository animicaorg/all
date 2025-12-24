from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.config import get_expected_genesis_hash
from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV
from core.chain.block_import import _theta_to_target, compute_header_hash
from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import P2PDeps
from p2p.errors import P2PError
from p2p.node.p2p_service import (
    P2PService,
    PeerMisbehavior,
    _PeerState,
    _SyncBlock,
    _SyncHeader,
)
from p2p.tests import tcp_multiaddr
from p2p.wire.encoding import encode_payload
from p2p.wire.frames import Framer
from p2p.wire.messages import Blocks, HeaderCompact, Headers, Hello

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_devnet_genesis(tmp_path: Path) -> Path:
    genesis_path = tmp_path / "genesis.devnet.json"
    if genesis_path.exists():
        return genesis_path
    base_genesis = json.loads(GENESIS_PATH.read_text(encoding="utf-8"))
    base_genesis["chainId"] = 1337
    base_genesis["network"] = "animica-devnet"
    consensus = base_genesis.get("consensus") or {}
    consensus["initialThetaMicro"] = 1
    base_genesis["consensus"] = consensus
    params_ref = base_genesis.get("paramsRef") or {}
    params_ref["path"] = str(
        Path(__file__).resolve().parents[2] / "spec" / "params.yaml"
    )
    base_genesis["paramsRef"] = params_ref
    genesis_path.write_text(json.dumps(base_genesis, indent=2), encoding="utf-8")
    return genesis_path


def _make_alt_genesis(tmp_path: Path) -> Path:
    genesis_path = tmp_path / "genesis.devnet.alt.json"
    if genesis_path.exists():
        return genesis_path
    base_genesis = json.loads(_make_devnet_genesis(tmp_path).read_text(encoding="utf-8"))
    alloc = base_genesis.get("alloc", [])
    if alloc:
        alloc[0]["balance"] = int(alloc[0].get("balance", 0)) + 1
    base_genesis["alloc"] = alloc
    genesis_path.write_text(json.dumps(base_genesis, indent=2), encoding="utf-8")
    return genesis_path


def _make_deps(tmp_path: Path, name: str) -> P2PDeps:
    db_path = tmp_path / f"{name}.db"
    genesis_path = _make_devnet_genesis(tmp_path)
    return P2PDeps.open(f"sqlite:///{db_path}", str(genesis_path))


def _make_child_block(sync_deps: P2PDeps) -> Block:
    height, head_hash = sync_deps.head()
    header = sync_deps.header_by_hash(head_hash) if head_hash else None
    if header is None:
        header = sync_deps.header_by_number(0)
    assert header is not None

    timestamp = int(getattr(header, "timestamp", 0)) + 1
    target = _theta_to_target(int(getattr(header, "thetaMicro", 0)))
    child = None
    for nonce in range(0, 10000):
        candidate = header.build_child(
            timestamp=timestamp,
            state_root=header.stateRoot,
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


def _make_child_block_from_header(parent) -> Block:
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


def test_mainnet_genesis_is_enforced_on_startup(tmp_path: Path) -> None:
    db_path = tmp_path / "genesis-mismatch.db"
    kv = SQLiteKV(str(db_path))
    block_db = BlockDB(kv)
    wrong_hash = b"\x11" * 32
    block_db.set_chain_id(1)
    block_db.set_canonical_head(0, wrong_hash)
    kv.close()

    with pytest.raises(P2PError, match="GENESIS_MISMATCH"):
        P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))


def test_genesis_mismatch_can_auto_reset(tmp_path: Path) -> None:
    from core.genesis.loader import load_and_init_genesis, load_genesis

    genesis_a = _make_devnet_genesis(tmp_path)
    genesis_b = _make_alt_genesis(tmp_path)
    db_path = tmp_path / "genesis-reset.db"
    db_uri = f"sqlite:///{db_path}"

    load_and_init_genesis(str(genesis_a), db_uri, log=False)

    with pytest.raises(P2PError, match="GENESIS_MISMATCH"):
        P2PDeps.open(db_uri, str(genesis_b))

    deps = P2PDeps.open(db_uri, str(genesis_b), allow_genesis_reset=True)
    _params, header = load_genesis(str(genesis_b))
    assert deps.db_genesis_hash == bytes(header.hash())


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


@pytest.mark.asyncio
async def test_block_import_order_no_missing_parent_stall(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "missing-parent")
    peer = _register_peer(node, "peer-order:0")
    peer.ready_for_sync = True
    peer.hello_done.set()
    peer.peer_id = "peer-order"
    peer.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "head_height": 0,
        "capabilities": ["sync"],
    }

    block1 = _make_child_block(deps_sync)
    block2 = _make_child_block_from_header(block1.header)

    payload2 = encode_payload(Blocks(blocks=[block2.to_cbor()]))
    await node._handle_blocks(peer, payload2)

    assert block2.header.hash() in node._sync_block_buffer
    assert node._sync_block_stalled_reason is None

    payload1 = encode_payload(Blocks(blocks=[block1.to_cbor()]))
    await node._handle_blocks(peer, payload1)

    height, _ = deps_sync.head()
    assert height == 2
    assert block2.header.hash() not in node._sync_block_buffer


def test_header_batch_must_anchor(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "anchor")
    block = _make_child_block(deps_sync)
    accepted, _reason = deps_sync.import_block(block)
    assert accepted

    header = HeaderCompact(
        hash=b"\x11" * 32,
        height=2,
        parent=b"\x22" * 32,
        theta_micro=1,
        timestamp=int(block.header.timestamp) + 1,
    )
    peer = _make_peer()

    accepted_hashes, reason = node._process_headers(peer, [header])

    assert accepted_hashes == []
    assert reason == "not_anchored"
    assert node._sync_peer_backoff_reason.get(peer.remote) == "not_anchored"
    assert node._queued_blocks_count() == 0


def test_headers_anchor_off_by_one_inclusive_response_ok(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "anchor-inclusive")
    block1 = _make_child_block(deps_sync)
    accepted, _reason = deps_sync.import_block(block1)
    assert accepted
    block2 = _make_child_block_from_header(block1.header)

    header1 = HeaderCompact(
        hash=block1.header.hash(),
        height=int(block1.header.height),
        parent=bytes(block1.header.parentHash),
        theta_micro=int(getattr(block1.header, "thetaMicro", 0)),
        timestamp=int(getattr(block1.header, "timestamp", 0)),
    )
    header2 = HeaderCompact(
        hash=block2.header.hash(),
        height=int(block2.header.height),
        parent=bytes(block2.header.parentHash),
        theta_micro=int(getattr(block2.header, "thetaMicro", 0)),
        timestamp=int(getattr(block2.header, "timestamp", 0)),
    )

    peer = _make_peer()
    accepted_hashes, reason = node._process_headers(peer, [header1, header2])

    assert reason is None
    assert accepted_hashes == [block2.header.hash()]
    assert node._sync_best_header is not None
    assert node._sync_best_header.hash == block2.header.hash()


def test_headers_anchor_exclusive_response_ok(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "anchor-exclusive")
    block1 = _make_child_block(deps_sync)
    accepted, _reason = deps_sync.import_block(block1)
    assert accepted
    block2 = _make_child_block_from_header(block1.header)

    header2 = HeaderCompact(
        hash=block2.header.hash(),
        height=int(block2.header.height),
        parent=bytes(block2.header.parentHash),
        theta_micro=int(getattr(block2.header, "thetaMicro", 0)),
        timestamp=int(getattr(block2.header, "timestamp", 0)),
    )

    peer = _make_peer()
    accepted_hashes, reason = node._process_headers(peer, [header2])

    assert reason is None
    assert accepted_hashes == [block2.header.hash()]
    assert node._sync_best_header is not None
    assert node._sync_best_header.hash == block2.header.hash()


def test_do_not_mark_wrong_chain_on_single_invalid_headers(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "anchor-strike")
    node._sync_peer_penalty_threshold = 99
    block = _make_child_block(deps_sync)
    accepted, _reason = deps_sync.import_block(block)
    assert accepted

    bad_header = HeaderCompact(
        hash=b"\x11" * 32,
        height=int(block.header.height) + 1,
        parent=b"\x22" * 32,
        theta_micro=1,
        timestamp=int(block.header.timestamp) + 1,
    )
    peer = _make_peer()

    accepted_hashes, reason = node._process_headers(peer, [bad_header])

    assert accepted_hashes == []
    assert reason == "not_anchored"
    assert node._sync_peer_backoff_reason.get(peer.remote) == "not_anchored"

    accepted_hashes, reason = node._process_headers(peer, [bad_header])

    assert accepted_hashes == []
    assert reason == "not_anchored"
    assert node._sync_peer_backoff_reason.get(peer.remote) == "not_anchored"


@pytest.mark.asyncio
async def test_self_peer_filtered(tmp_path: Path) -> None:
    deps_sync = _make_deps(tmp_path, "self-peer")
    node = P2PService(
        listen_addrs=["/ip4/127.0.0.1/tcp/30333"],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps_sync,
        peerstore_path=str(tmp_path / "self-peer" / "p2p"),
    )
    peer = _register_peer(node, "127.0.0.1:30333")
    peer.stream = AsyncMock()
    hello = Hello(
        version="2",
        agent="animica-p2p/test",
        chain_id=node.chain_id,
        listen_port=30333,
        listen_addrs=["127.0.0.1:30333"],
        genesis_hash=node._genesis_hash(),
        fork_id=node._fork_id(),
        consensus_id=node._consensus_id(),
        protocol_version=node._protocol_version(),
        genesis_identity=node._genesis_identity(),
        network_params_hash=node._network_params_hash(),
        peer_id=b"\x11" * 32,
        head_height=0,
        head_hash=b"\x00" * 32,
        alg_policy_root=b"",
        capabilities=["sync"],
        timestamp=0,
    )

    payload = encode_payload(hello)
    with pytest.raises(PeerMisbehavior, match="self_peer"):
        await node._handle_hello(peer, payload)


def test_sync_status_head_hash_matches_chain_head(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "head-hash")
    height, header = deps_sync.head()
    assert header is not None

    head_tuple = deps_sync._block_db.get_head()
    assert head_tuple is not None
    expected_hash = "0x" + bytes(head_tuple[1]).hex()
    node._sync_best_header = _SyncHeader(
        hash=b"\x99" * 32,
        parent_hash=b"\x00" * 32,
        height=999,
        theta_micro=0,
        timestamp=0,
    )
    snap = node.sync_status_snapshot()

    assert snap.head_height == height
    assert snap.head_hash == expected_hash
    assert snap.best_block_hash == expected_hash


def test_sync_status_phase_not_synced_when_unsynchronized(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "phase-unsynced")
    block = _make_child_block(deps_sync)
    accepted, _reason = deps_sync.import_block(block)
    assert accepted

    snap = node.sync_status_snapshot()

    assert snap.synchronized is False
    assert snap.phase != "SYNCED"


def test_sync_status_synced_invariants(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "phase-synced")
    block = _make_child_block(deps_sync)
    accepted, _reason = deps_sync.import_block(block)
    assert accepted

    node._sync_best_header = _SyncHeader(
        hash=block.header.hash(),
        parent_hash=bytes(block.header.parentHash),
        height=int(block.header.height),
        theta_micro=int(getattr(block.header, "thetaMicro", 0)),
        timestamp=int(getattr(block.header, "timestamp", 0)),
    )

    peer = _register_peer(node, "peer-synced:0")
    peer.peer_id = "peer-synced"
    peer.hello_done.set()
    peer.ready_for_sync = True
    peer.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
        "head_height": int(block.header.height),
        "head_hash": block.header.hash(),
    }

    snap = node.sync_status_snapshot()

    assert snap.synchronized is True
    assert snap.phase == "SYNCED"
    assert snap.queued_blocks_count == 0
    assert snap.in_flight_headers == 0
    assert snap.in_flight_blocks == 0
    assert snap.best_header_height <= snap.head_height
    assert snap.best_block_height == snap.head_height


def test_sync_status_not_synced_with_inflight_headers(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "phase-inflight")
    block = _make_child_block(deps_sync)
    accepted, _reason = deps_sync.import_block(block)
    assert accepted

    node._sync_best_header = _SyncHeader(
        hash=block.header.hash(),
        parent_hash=bytes(block.header.parentHash),
        height=int(block.header.height),
        theta_micro=int(getattr(block.header, "thetaMicro", 0)),
        timestamp=int(getattr(block.header, "timestamp", 0)),
    )
    node._sync_inflight_headers = 1

    peer = _register_peer(node, "peer-inflight:0")
    peer.peer_id = "peer-inflight"
    peer.hello_done.set()
    peer.ready_for_sync = True
    peer.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
        "head_height": int(block.header.height),
        "head_hash": block.header.hash(),
    }

    snap = node.sync_status_snapshot()

    assert snap.synchronized is False
    assert snap.phase != "SYNCED"


def test_wrong_chain_peer_is_ineligible(tmp_path: Path) -> None:
    node, _deps_sync = _make_service(tmp_path, "wrong-chain")
    peer = _register_peer(node, "peer-wrong:0")
    peer.peer_id = "peer-wrong"
    peer.hello_done.set()
    peer.ready_for_sync = True
    peer.hello = {
        "chain_id": node.chain_id + 1,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
        "head_height": 1,
        "head_hash": b"\x00" * 32,
    }

    ok, reason = node._sync_peer_eligibility(peer, now=time.time())

    assert ok is False
    assert reason == "chain_mismatch"


@pytest.mark.asyncio
async def test_phantom_cursor_reset_clears_inflight_and_restarts(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "phantom-reset")
    block = _make_child_block(deps_sync)
    accepted, _reason = deps_sync.import_block(block)
    assert accepted

    phantom_hash = b"\x22" * 32
    node._sync_best_header = _SyncHeader(
        hash=phantom_hash,
        parent_hash=b"\x00" * 32,
        height=1,
        theta_micro=0,
        timestamp=0,
    )
    node._sync_inflight_blocks[phantom_hash] = 0.0
    node._sync_inflight_peers[phantom_hash] = "peer-1:0"
    node._sync_inflight_header_requests[("peer-1:0", "req-1")] = 0.0
    node._sync_inflight_headers = 1

    peer = _register_peer(node, "peer-1:0")
    peer.ready_for_sync = True
    peer.hello_done.set()
    peer.peer_id = "peer-1"
    peer.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "head_height": 10,
        "capabilities": ["sync"],
    }
    node._fetch_headers = AsyncMock(return_value=[])

    await node._sync_once(force=True)

    _height, head = deps_sync.head()
    head_hash = head if isinstance(head, (bytes, bytearray)) else head.hash()
    assert node._sync_best_header is not None
    assert node._sync_best_header.hash == head_hash
    assert node._sync_inflight_headers == 0
    assert not node._sync_inflight_blocks
    assert len(node._sync_header_queue) == 0
    node._fetch_headers.assert_called()


@pytest.mark.asyncio
async def test_header_response_attribution_requires_request_match(
    tmp_path: Path,
) -> None:
    node, _deps_sync = _make_service(tmp_path, "header-attrib")
    peer_a = _register_peer(node, "peer-a:0")
    peer_b = _register_peer(node, "peer-b:0")

    fut = asyncio.get_event_loop().create_future()
    peer_a.pending_headers = fut
    peer_a.pending_header_request_id = "req-123"
    node._sync_inflight_header_requests[(peer_a.remote, "req-123")] = 0.0
    node._sync_inflight_headers = 1

    header = HeaderCompact(
        hash=b"\x01" * 32,
        height=1,
        parent=b"\x00" * 32,
        theta_micro=1,
        timestamp=1,
    )
    payload = encode_payload(Headers(headers=[header]))

    await node._handle_headers(peer_b, payload)

    assert node._sync_last_header_response_peer is None
    assert node._sync_inflight_headers == 1
    assert peer_a.pending_headers is fut


@pytest.mark.asyncio
async def test_at_tip_requires_network_confirmation(tmp_path: Path) -> None:
    node, deps_sync = _make_service(tmp_path, "tip-confirm")
    block = _make_child_block(deps_sync)
    accepted, _reason = deps_sync.import_block(block)
    assert accepted

    peer = _register_peer(node, "peer-tip:0")
    peer.ready_for_sync = True
    peer.hello_done.set()
    peer.peer_id = "peer-tip"
    peer.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "head_height": 5000,
        "capabilities": ["sync"],
    }

    node._fetch_headers = AsyncMock(return_value=[])
    await node._sync_once(force=True)

    assert node._sync_last_header_error != "at_tip"


def test_sync_status_head_hash_matches_mainnet_genesis(tmp_path: Path) -> None:
    expected = get_expected_genesis_hash(1)
    assert expected is not None
    expected_hex = "0x" + expected.hex()

    class _StubHeader:
        def __init__(self, h: bytes) -> None:
            self._h = h
            self.height = 0

        def hash(self) -> bytes:
            return self._h

    class _StubDeps:
        def __init__(self, h: bytes) -> None:
            self._h = h

        def head(self):
            return 0, _StubHeader(self._h)

    node = P2PService(
        listen_addrs=[tcp_multiaddr(0)],
        seeds=[],
        chain_id=1,
        deps=_StubDeps(expected),
        peerstore_path=str(tmp_path / "head-genesis" / "p2p"),
    )

    snap = node.sync_status_snapshot()

    assert snap.head_height == 0
    assert snap.head_hash == expected_hex


def test_sync_status_head_hash_stable_without_progress(tmp_path: Path) -> None:
    node, _deps_sync = _make_service(tmp_path, "head-stable")

    first = node.sync_status_snapshot()
    second = node.sync_status_snapshot()

    assert first.head_height == second.head_height
    assert first.head_hash == second.head_hash


@pytest.mark.asyncio
async def test_peer_rejected_on_genesis_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node, _deps_sync = _make_service(tmp_path, "genesis-peer")
    peer = _register_peer(node, "203.0.113.50:30333")

    async def _noop_send(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(node, "_send", _noop_send)

    hello = Hello(
        version="2",
        agent="test",
        chain_id=node.chain_id,
        listen_port=0,
        listen_addrs=[],
        genesis_hash=b"\x22" * 32,
        fork_id=node._fork_id(),
        consensus_id=node._consensus_id(),
        protocol_version=node._protocol_version(),
        genesis_identity=node._genesis_identity(),
        network_params_hash=node._network_params_hash(),
        peer_id=b"\x33" * 32,
        head_height=0,
        head_hash=b"\x00" * 32,
        capabilities=["sync"],
        timestamp=0,
    )
    payload = encode_payload(
        {k: getattr(hello, k) for k in hello.__dataclass_fields__.keys() if k != "msg_id"}
    )

    with pytest.raises(PeerMisbehavior) as excinfo:
        await node._handle_hello(peer, payload)

    err = excinfo.value
    assert err.points == node._score_points["wrong_genesis"]
    assert node._stats["p2p_peers_rejected_genesis_mismatch"] == 1


@pytest.mark.asyncio
async def test_empty_headers_response_is_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node, _deps_sync = _make_service(tmp_path, "empty-headers")
    peer = _register_peer(node, "203.0.113.10:30333")
    peer.peer_id = "peer-1"
    peer.hello_done.set()
    peer.ready_for_sync = True
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
        "head_hash": b"\x11" * 32,
    }
    node._sync_no_headers_threshold = 1

    async def _fake_fetch_headers(_peer: _PeerState):
        return []

    monkeypatch.setattr(node, "_fetch_headers", _fake_fetch_headers)

    await node._sync_once(force=True)

    assert node._sync_fatal_error is None
    assert node._sync_last_header_response_count == 0
    assert node._sync_last_header_error != "peer_at_genesis"


@pytest.mark.asyncio
async def test_empty_headers_at_tip_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node, deps_sync = _make_service(tmp_path, "empty-at-tip")
    peer = _register_peer(node, "203.0.113.11:30333")
    peer.peer_id = "peer-2"
    peer.hello_done.set()
    peer.ready_for_sync = True
    peer.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
        "head_height": 1,
        "head_hash": b"\x22" * 32,
    }
    node._sync_no_headers_threshold = 1
    peer_b = _register_peer(node, "203.0.113.12:30333")
    peer_b.peer_id = "peer-3"
    peer_b.hello_done.set()
    peer_b.ready_for_sync = True
    peer_b.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
        "head_height": 1,
        "head_hash": b"\x22" * 32,
    }

    block = _make_child_block(deps_sync)
    accepted, reason = deps_sync.import_block(block)
    assert accepted, reason

    async def _fake_fetch_headers(_peer: _PeerState):
        return []

    monkeypatch.setattr(node, "_fetch_headers", _fake_fetch_headers)

    await node._sync_once(force=True)

    assert node._sync_fatal_error is None
    assert node._sync_last_header_error == "at_tip"
    assert node._sync_peer_penalties == {}
    assert node._sync_peer_backoff_reason.get(peer.remote) is None
    snap = node.sync_status_snapshot()
    assert snap.phase != "STALLED"


@pytest.mark.asyncio
async def test_block_requests_sequential_by_height(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node, _deps_sync = _make_service(tmp_path, "block-order")
    peer = _register_peer(node, "203.0.113.12:30333")
    peer.peer_id = "peer-3"
    peer.hello_done.set()
    peer.ready_for_sync = True
    peer.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
        "head_height": 3,
        "head_hash": b"\x33" * 32,
    }

    h1 = b"\x01" * 32
    h2 = b"\x02" * 32
    h3 = b"\x03" * 32
    node._sync_headers[h1] = _SyncHeader(
        hash=h1, parent_hash=b"\x00" * 32, height=1, theta_micro=0, timestamp=1
    )
    node._sync_headers[h2] = _SyncHeader(
        hash=h2, parent_hash=h1, height=2, theta_micro=0, timestamp=2
    )
    node._sync_headers[h3] = _SyncHeader(
        hash=h3, parent_hash=h2, height=3, theta_micro=0, timestamp=3
    )
    node._sync_best_header = node._sync_headers[h3]
    node._sync_max_inflight = 3
    node._sync_block_queue.extend([h3, h1, h2])
    node._sync_block_queue_set.update([h1, h2, h3])
    node._sync_block_queue_heights.update({h1: 1, h2: 2, h3: 3})

    requested: list[bytes] = []

    async def _capture_send(_peer: _PeerState, _msg_id, payload):
        requested.extend(list(payload.by_hash))

    monkeypatch.setattr(node, "_send", _capture_send)

    await node._schedule_block_requests(peer)

    assert requested == [h1, h2, h3]


@pytest.mark.asyncio
async def test_missing_parent_enqueues_parent_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node, _deps_sync = _make_service(tmp_path, "missing-parent")
    peer = _register_peer(node, "203.0.113.13:30333")
    peer.peer_id = "peer-4"
    peer.hello_done.set()
    peer.ready_for_sync = True
    peer.hello = {
        "chain_id": node.chain_id,
        "genesis_hash": node._genesis_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "capabilities": ["sync"],
        "head_height": 2,
        "head_hash": b"\x44" * 32,
    }

    parent = b"\x10" * 32
    child = b"\x20" * 32
    node._sync_headers[parent] = _SyncHeader(
        hash=parent, parent_hash=b"\x00" * 32, height=1, theta_micro=0, timestamp=1
    )
    node._sync_headers[child] = _SyncHeader(
        hash=child, parent_hash=parent, height=2, theta_micro=0, timestamp=2
    )
    node._sync_block_queue.extend([child])
    node._sync_block_queue_set.add(child)
    node._sync_block_queue_heights[child] = 2

    sync_block = _SyncBlock(block=b"", hash=child, parent_hash=parent, origin_peer=peer.remote)
    node._handle_missing_parent(peer, sync_block)

    requested: list[bytes] = []

    async def _capture_send(_peer: _PeerState, _msg_id, payload):
        requested.extend(list(payload.by_hash))

    monkeypatch.setattr(node, "_send", _capture_send)

    await node._schedule_block_requests(peer)

    assert requested[0] == parent
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
        genesis_hash=node._genesis_hash(),
        fork_id=node._fork_id(),
        consensus_id=node._consensus_id(),
        protocol_version=node._protocol_version(),
        genesis_identity=node._genesis_identity(),
        network_params_hash=node._network_params_hash(),
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
