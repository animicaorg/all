from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from core.chain.block_import import _theta_to_target, compute_header_hash
from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState
from p2p.wire.encoding import encode_payload
from p2p.wire.messages import Blocks

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"

os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")


def _make_deps(tmp_path: Path, name: str) -> tuple[P2PDeps, AsyncP2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, AsyncP2PDeps(sync_deps)


def _build_child_block(parent_header, *, timestamp: int) -> Block:
    target = _theta_to_target(int(getattr(parent_header, "thetaMicro", 0)))
    child = None
    for nonce in range(0, 10000):
        candidate = parent_header.build_child(
            timestamp=timestamp,
            state_root=parent_header.stateRoot,
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


async def _wait_for_height(
    deps: AsyncP2PDeps, height: int, timeout: float = 20.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur, _ = await deps.head()
        if cur >= height:
            return True
        await asyncio.sleep(0.2)
    return False


@pytest.mark.asyncio
async def test_orphan_block_imports_after_parent(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "orphan")
    node = P2PService(
        listen_addrs=[],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "orphan" / "p2p"),
    )
    try:
        genesis = deps_sync.header_by_number(0)
        assert genesis is not None
        ts = int(getattr(genesis, "timestamp", 0))
        block1 = _build_child_block(genesis, timestamp=ts + 1)
        block2 = _build_child_block(block1.header, timestamp=ts + 2)

        peer = _PeerState(
            session_id="orphan-peer",
            remote="orphan-peer:0",
            direction="inbound",
            conn=None,
            stream=None,
            framer=None,
            write_lock=asyncio.Lock(),
        )

        payload_child = encode_payload(Blocks(blocks=[block2.to_cbor()]))
        await node._handle_blocks(peer, payload_child)

        payload_parent = encode_payload(Blocks(blocks=[block1.to_cbor()]))
        await node._handle_blocks(peer, payload_parent)

        assert await _wait_for_height(deps, 2, timeout=10.0)
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_reorg_to_heavier_fork(tmp_path: Path) -> None:
    deps_sync, deps = _make_deps(tmp_path, "fork")

    node = P2PService(
        listen_addrs=[],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "fork" / "p2p"),
    )
    try:
        genesis = deps_sync.header_by_number(0)
        assert genesis is not None
        ts = int(getattr(genesis, "timestamp", 0))
        block1 = _build_child_block(genesis, timestamp=ts + 1)
        block2 = _build_child_block(block1.header, timestamp=ts + 2)
        block3 = _build_child_block(block2.header, timestamp=ts + 3)

        peer = _PeerState(
            session_id="fork-peer",
            remote="fork-peer:0",
            direction="inbound",
            conn=None,
            stream=None,
            framer=None,
            write_lock=asyncio.Lock(),
        )
        for blk in (block1, block2, block3):
            payload = encode_payload(Blocks(blocks=[blk.to_cbor()]))
            await node._handle_blocks(peer, payload)

        fork_block2 = _build_child_block(block1.header, timestamp=ts + 4)
        fork_block3 = _build_child_block(fork_block2.header, timestamp=ts + 5)
        fork_block4 = _build_child_block(fork_block3.header, timestamp=ts + 6)
        for blk in (fork_block2, fork_block3, fork_block4):
            payload = encode_payload(Blocks(blocks=[blk.to_cbor()]))
            await node._handle_blocks(peer, payload)

        await asyncio.sleep(0)
        _, head_hash = await deps.head()
        assert head_hash == fork_block4.header.hash()
    finally:
        await node.stop()
