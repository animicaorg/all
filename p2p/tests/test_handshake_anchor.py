from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.chain.block_import import _theta_to_target, compute_header_hash
from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, PeerMisbehavior, _PeerState
from p2p.tests import tcp_multiaddr
from p2p.wire.encoding import encode_payload
from p2p.wire.frames import Framer
from p2p.wire.messages import Headers, Hello

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


def _make_deps(tmp_path: Path, name: str) -> P2PDeps:
    db_path = tmp_path / f"{name}.db"
    genesis_path = _make_devnet_genesis(tmp_path)
    return P2PDeps.open(f"sqlite:///{db_path}", str(genesis_path))


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


def _register_peer(node: P2PService, remote: str) -> _PeerState:
    session = node._peer_registry.register(remote, "inbound")
    peer = _PeerState(
        session_id=session.session_id,
        remote=remote,
        direction="inbound",
        conn=None,
        stream=AsyncMock(),
        framer=Framer(aead=None),
        write_lock=asyncio.Lock(),
    )
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
async def test_handshake_accepts_hello_after_headers(tmp_path: Path) -> None:
    node, _deps = _make_service(tmp_path, "hello-order")
    peer = _register_peer(node, "peer-hello:0")

    await node._handle_headers(peer, encode_payload(Headers(headers=[])))

    genesis_header_hash = node._genesis_header_hash()
    genesis_block_hash = node._genesis_block_hash()
    hello = Hello(
        version="2",
        agent="test-node",
        chain_id=node.chain_id,
        listen_port=0,
        listen_addrs=[],
        genesis_hash=genesis_header_hash,
        genesis_header_hash=genesis_header_hash,
        genesis_block_hash=genesis_block_hash,
        fork_id=node._fork_id(),
        consensus_id=node._consensus_id(),
        protocol_version=node._protocol_version(),
        genesis_identity=node._genesis_identity(),
        network_params_hash=node._network_params_hash(),
        peer_id=b"\x11" * 32,
        head_height=1,
        head_hash=genesis_header_hash,
        alg_policy_root=b"",
        capabilities=["sync"],
        timestamp=0,
    )
    await node._handle_hello(peer, encode_payload(hello))

    ok, reason = node._sync_peer_eligibility(peer)
    assert ok, reason


def test_genesis_anchor_allows_sync_from_height_zero(tmp_path: Path) -> None:
    node_a, deps_a = _make_service(tmp_path, "anchor-a")
    node_b, _deps_b = _make_service(tmp_path, "anchor-b")

    for _ in range(200):
        height, head_hash = deps_a.head()
        header = deps_a.header_by_hash(head_hash) if head_hash else None
        if header is None:
            header = deps_a.header_by_number(0)
        assert header is not None
        block = _make_child_block(header)
        ok, reason = deps_a.import_block(block)
        assert ok, reason

    head_height, head_hash_hex = node_a._local_head()
    head_hash = bytes.fromhex(head_hash_hex[2:]) if head_hash_hex else b"\x00" * 32

    peer = _register_peer(node_b, "peer-anchor:0")
    peer.hello = {
        "chain_id": node_b.chain_id,
        "genesis_header_hash": node_b._genesis_header_hash(),
        "genesis_block_hash": node_b._genesis_block_hash(),
        "genesis_hash": node_b._genesis_header_hash(),
        "fork_id": node_b._fork_id(),
        "consensus_id": node_b._consensus_id(),
        "protocol_version": node_b._protocol_version(),
        "genesis_identity": node_b._genesis_identity(),
        "network_params_hash": node_b._network_params_hash(),
        "head_height": head_height,
        "head_hash": head_hash,
        "capabilities": ["sync"],
    }

    target = head_height
    for _ in range(10):
        if node_b._sync_best_header and node_b._sync_best_header.height >= target:
            break
        locator = node_b._build_locator()
        headers = node_a._headers_after_locator(locator, limit=64)
        if not headers:
            break
        hashes, reason, discard = node_b._process_headers(peer, headers)
        assert reason is None, f"Expected None, got {reason} with discard={discard}"
        assert hashes

    assert node_b._sync_best_header is not None
    assert node_b._sync_best_header.height == target


@pytest.mark.asyncio
async def test_mismatch_rejects_peer(tmp_path: Path) -> None:
    node, _deps = _make_service(tmp_path, "mismatch")
    peer = _register_peer(node, "peer-bad:0")

    bad_genesis = b"\x22" * 32
    hello = Hello(
        version="2",
        agent="test-node",
        chain_id=node.chain_id,
        listen_port=0,
        listen_addrs=[],
        genesis_hash=bad_genesis,
        genesis_header_hash=bad_genesis,
        genesis_block_hash=bad_genesis,
        fork_id=node._fork_id(),
        consensus_id=node._consensus_id(),
        protocol_version=node._protocol_version(),
        genesis_identity=node._genesis_identity(),
        network_params_hash=node._network_params_hash(),
        peer_id=b"\x33" * 32,
        head_height=1,
        head_hash=node._genesis_header_hash(),
        alg_policy_root=b"",
        capabilities=["sync"],
        timestamp=0,
    )

    with pytest.raises(PeerMisbehavior) as exc:
        await node._handle_hello(peer, encode_payload(hello))
    assert exc.value.reason == "genesis_mismatch"
