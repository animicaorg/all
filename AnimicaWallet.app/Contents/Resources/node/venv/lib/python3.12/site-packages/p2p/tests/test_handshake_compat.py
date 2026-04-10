from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, PeerMisbehavior, _PeerState
from p2p.tests import tcp_multiaddr
from p2p.wire.encoding import encode_payload
from p2p.wire.frames import Framer
from p2p.wire.messages import Hello

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


def _make_service(tmp_path: Path, name: str) -> P2PService:
    db_path = tmp_path / f"{name}.db"
    genesis_path = _make_devnet_genesis(tmp_path)
    deps = P2PDeps.open(f"sqlite:///{db_path}", str(genesis_path))
    return P2PService(
        listen_addrs=[tcp_multiaddr(0)],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / name / "p2p"),
    )


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


@pytest.mark.asyncio
async def test_legacy_handshake_missing_fields_is_accepted(tmp_path: Path) -> None:
    node = _make_service(tmp_path, "legacy-handshake")
    peer = _register_peer(node, "peer-legacy:0")

    genesis_header_hash = node._genesis_header_hash()
    payload = {
        "version": "1",
        "agent": "legacy-node",
        "chain_id": node.chain_id,
        "listen_port": 0,
        "listen_addrs": [],
        "genesis_hash": genesis_header_hash,
        "peer_id": b"\x11" * 32,
        "head_height": 1,
        "head_hash": genesis_header_hash,
        "capabilities": ["sync"],
        "timestamp": 0,
    }
    await node._handle_hello(peer, encode_payload(payload))

    ok, reason = node._sync_peer_eligibility(peer)
    assert ok, reason
    assert reason in {"eligible", "legacy_handshake"}
    assert "tx_relay_v2" not in peer.negotiated_caps


@pytest.mark.asyncio
async def test_unknown_capabilities_do_not_block_handshake(tmp_path: Path) -> None:
    node = _make_service(tmp_path, "unknown-caps")
    peer = _register_peer(node, "peer-caps:0")

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
        peer_id=b"\x22" * 32,
        head_height=1,
        head_hash=genesis_header_hash,
        alg_policy_root=b"",
        capabilities=["sync", "snapshot_v2", "totally_new_feature"],
        timestamp=0,
    )

    await node._handle_hello(peer, encode_payload(hello))

    ok, reason = node._sync_peer_eligibility(peer)
    assert ok, reason
    assert "sync" in peer.negotiated_caps
    assert "totally_new_feature" not in peer.negotiated_caps


@pytest.mark.asyncio
async def test_required_caps_missing_is_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANIMICA_P2P_REQUIRED_CAPS", "tx_relay_v2")
    node = _make_service(tmp_path, "required-caps")
    peer = _register_peer(node, "peer-required:0")

    genesis_header_hash = node._genesis_header_hash()
    payload = {
        "version": "2",
        "agent": "missing-caps-node",
        "chain_id": node.chain_id,
        "listen_port": 0,
        "listen_addrs": [],
        "genesis_hash": genesis_header_hash,
        "genesis_header_hash": genesis_header_hash,
        "genesis_block_hash": node._genesis_block_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "peer_id": b"\x33" * 32,
        "head_height": 1,
        "head_hash": genesis_header_hash,
        "capabilities": ["sync"],
        "timestamp": 0,
    }

    with pytest.raises(PeerMisbehavior):
        await node._handle_hello(peer, encode_payload(payload))
    assert node._caps_failures_by_reason.get("caps_missing") == 1
