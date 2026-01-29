from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.chain.block_import import _theta_to_target, compute_header_hash
from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService, PeerMisbehavior, _PeerState
from p2p.wire.encoding import encode_payload
from p2p.wire.frames import Framer
from p2p.wire.messages import Blocks, HeaderCompact, Hello

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


async def _wait_for_height(deps: AsyncP2PDeps, height: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur, _ = await deps.head()
        if cur >= height:
            return True
        await asyncio.sleep(0.2)
    return False


@pytest.mark.asyncio
async def test_two_nodes_same_consensus_sync_blocks(tmp_path: Path) -> None:
    genesis_path = _make_devnet_genesis(tmp_path)
    deps_a = P2PDeps.open(f"sqlite:///{tmp_path / 'a.db'}", str(genesis_path))
    deps_b = P2PDeps.open(f"sqlite:///{tmp_path / 'b.db'}", str(genesis_path))
    deps_b_async = AsyncP2PDeps(deps_b)

    node_b = P2PService(
        listen_addrs=[],
        seeds=[],
        chain_id=deps_b.chain_id,
        deps=deps_b_async,
        peerstore_path=str(tmp_path / "b" / "p2p"),
    )

    try:
        parent = deps_a.header_by_number(0)
        assert parent is not None
        blocks: list[Block] = []
        for _ in range(6):
            block = _make_child_block(parent)
            ok, reason = deps_a.import_block(block)
            assert ok, reason
            blocks.append(block)
            parent = block.header

        peer = _PeerState(
            session_id="peer-a",
            remote="peer-a:0",
            direction="inbound",
            conn=None,
            stream=AsyncMock(),
            framer=Framer(aead=None),
            write_lock=asyncio.Lock(),
        )
        peer.hello_done.set()
        peer.hello = {
            "version": "2",
            "chain_id": node_b.chain_id,
            "genesis_hash": node_b._genesis_hash(),
            "genesis_header_hash": node_b._genesis_header_hash(),
            "genesis_block_hash": node_b._genesis_block_hash(),
            "fork_id": node_b._fork_id(),
            "consensus_id": node_b._consensus_id(),
            "protocol_version": node_b._protocol_version(),
            "genesis_identity": node_b._genesis_identity(),
            "network_params_hash": node_b._network_params_hash(),
            "capabilities": ["sync"],
            "head_height": 6,
            "head_hash": blocks[-1].header.hash(),
        }

        headers = [
            HeaderCompact(
                hash=blk.header.hash(),
                height=int(blk.header.height),
                parent=bytes(blk.header.parentHash),
                theta_micro=int(blk.header.thetaMicro),
                timestamp=int(blk.header.timestamp),
            )
            for blk in blocks
        ]

        accepted, err, discard = node_b._process_headers(peer, headers)
        assert err is None
        assert discard == {}
        assert len(accepted) == 6

        for blk in blocks:
            payload = encode_payload(Blocks(blocks=[blk.to_cbor()]))
            await node_b._handle_blocks(peer, payload)

        assert await _wait_for_height(deps_b_async, 6, timeout=10.0)
    finally:
        await node_b.stop()


@pytest.mark.asyncio
async def test_consensus_mismatch_disconnects_early(tmp_path: Path) -> None:
    genesis_path = _make_devnet_genesis(tmp_path)
    deps = P2PDeps.open(f"sqlite:///{tmp_path / 'c.db'}", str(genesis_path))
    deps_async = AsyncP2PDeps(deps)
    node = P2PService(
        listen_addrs=[],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps_async,
        peerstore_path=str(tmp_path / "c" / "p2p"),
    )

    peer = _PeerState(
        session_id="peer-bad",
        remote="peer-bad:0",
        direction="inbound",
        conn=None,
        stream=AsyncMock(),
        framer=Framer(aead=None),
        write_lock=asyncio.Lock(),
    )

    hello = Hello(
        version="2",
        agent="animica-test",
        chain_id=node.chain_id,
        listen_port=0,
        listen_addrs=[],
        genesis_hash=node._genesis_header_hash(),
        genesis_header_hash=node._genesis_header_hash(),
        genesis_block_hash=node._genesis_block_hash(),
        fork_id=node._fork_id(),
        consensus_id="consensus/bad",
        protocol_version=node._protocol_version(),
        genesis_identity=node._genesis_identity(),
        network_params_hash=node._network_params_hash(),
        peer_id=b"\x11" * 32,
        head_height=0,
        head_hash=b"\x00" * 32,
        capabilities=["sync"],
        timestamp=int(time.time()),
    )
    payload = encode_payload(hello)

    with pytest.raises(PeerMisbehavior) as exc:
        await node._handle_hello(peer, payload)
    assert exc.value.reason == "consensus_mismatch"
    assert peer.invalid_blocks == 0
