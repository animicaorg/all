from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from core.chain.block_import import _theta_to_target, compute_header_hash
from core.genesis.loader import load_genesis
from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState
from p2p.tests import tcp_multiaddr
from p2p.wire.encoding import encode_payload
from p2p.wire.frames import Framer
from p2p.wire.message_ids import MsgID
from p2p.wire.messages import Blocks, Headers

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_deps_mainnet(tmp_path: Path, name: str) -> P2PDeps:
    db_path = tmp_path / f"{name}.db"
    return P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))


def _make_service(tmp_path: Path, name: str) -> tuple[P2PService, P2PDeps]:
    deps_sync = _make_deps_mainnet(tmp_path, name)
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
        stream=None,
        framer=Framer(aead=None),
        write_lock=asyncio.Lock(),
    )
    node._peers[remote] = peer
    node._peers_by_session[peer.session_id] = peer
    return peer


def _setup_peer_hello(node: P2PService, peer: _PeerState, head_height: int, head_hash: bytes) -> None:
    peer.ready_for_sync = True
    peer.hello_done.set()
    peer.peer_id = peer.peer_id or "peer-mainnet"
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
        "capabilities": ["sync"],
        "head_height": head_height,
        "head_hash": head_hash,
    }


def _make_child_block(parent) -> Block:
    timestamp = int(getattr(parent, "timestamp", 0)) + 1
    theta_micro = int(getattr(parent, "thetaMicro", 0))
    target = _theta_to_target(theta_micro)
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
async def test_mainnet_two_nodes_sync_to_tip(tmp_path: Path) -> None:
    params, genesis_header = load_genesis(str(GENESIS_PATH))
    _ = params, genesis_header

    node_a, deps_a = _make_service(tmp_path, "sync-a")
    node_b, _deps_b = _make_service(tmp_path, "sync-b")

    for _ in range(6):
        _, head_hash = deps_a.head()
        header = deps_a.header_by_hash(head_hash) if head_hash else None
        if header is None:
            header = deps_a.header_by_number(0)
        assert header is not None
        block = _make_child_block(header)
        ok, reason = deps_a.import_block(block)
        assert ok, reason

    head_height, head_hash_hex = node_a._local_head()
    head_hash = bytes.fromhex(head_hash_hex[2:]) if head_hash_hex else b"\x00" * 32

    peer = _register_peer(node_b, "peer-mainnet:0")
    _setup_peer_hello(node_b, peer, head_height=head_height, head_hash=head_hash)

    async def _fake_send(peer_obj: _PeerState, msg_id: MsgID, payload) -> None:
        if msg_id == MsgID.GET_HEADERS:
            locator = [bytes(h) for h in payload.locator]
            headers = node_a._headers_after_locator(
                locator, limit=int(payload.max_headers or 64)
            )
            if peer_obj.pending_headers and not peer_obj.pending_headers.done():
                peer_obj.pending_headers.set_result(Headers(headers=headers))
            return
        if msg_id == MsgID.GET_BLOCKS:
            blocks = []
            for h in payload.by_hash:
                raw = node_a._get_block_raw(bytes(h))
                if raw:
                    blocks.append(raw)
            if blocks:
                await node_b._handle_blocks(peer_obj, encode_payload(Blocks(blocks=blocks)))
            return

    node_b._send = _fake_send  # type: ignore[assignment]

    deadline = time.time() + 8.0
    while time.time() < deadline:
        headers = await node_b._fetch_headers(peer)
        if headers is None:
            await asyncio.sleep(0.05)
            continue
        accepted, reason, discard = node_b._process_headers(peer, headers)
        assert reason is None, (reason, discard)
        await node_b._schedule_block_requests(peer)
        if node_b._local_head()[0] >= 6:
            break
        await asyncio.sleep(0.05)

    assert node_b._local_head()[0] == 6
    assert "anchor_mismatch" not in (node_b._sync_last_headers_discard_reason_counts or {})
    assert node_b._sync_last_locator_summary is not None
    assert node_b._sync_last_locator_summary.get("count", 0) >= 2
