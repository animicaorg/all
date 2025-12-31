from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path

import pytest

from core.utils.hash import sha3_256
from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService
from p2p.tests import free_port, tcp_multiaddr, wait_for

os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
os.environ.setdefault("ANIMICA_P2P_PRIVATE_NETWORK", "1")
os.environ.setdefault("ANIMICA_P2P_ALLOW_SELF_PEER", "1")

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


@dataclass
class _Mempool:
    raw_by_hash: dict[bytes, bytes]

    def admit_tx(self, raw: bytes) -> tuple[bool, str | None]:
        tx_hash = sha3_256(raw)
        if tx_hash not in self.raw_by_hash:
            self.raw_by_hash[tx_hash] = bytes(raw)
        return True, None

    def get_tx_raw(self, tx_hash: bytes) -> bytes | None:
        return self.raw_by_hash.get(tx_hash)

    def list_pending_hashes(self, limit: int = 512) -> list[bytes]:
        return list(self.raw_by_hash.keys())[:limit]


class _RelayDeps:
    def __init__(self, base: P2PDeps, mempool: _Mempool) -> None:
        self._sync = base
        self._mempool = mempool
        self.chain_id = base.chain_id

    def __getattr__(self, name: str):
        return getattr(self._sync, name)

    def admit_tx(self, raw: bytes) -> tuple[bool, str | None]:
        return self._mempool.admit_tx(raw)

    def get_tx_raw(self, tx_hash: bytes) -> bytes | None:
        return self._mempool.get_tx_raw(tx_hash)

    def list_pending_hashes(self, limit: int = 512) -> list[bytes]:
        return self._mempool.list_pending_hashes(limit)


async def _start_node(tmp_path: Path, name: str, port: int) -> tuple[P2PService, _Mempool]:
    db_path = tmp_path / f"{name}.db"
    base = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    mempool = _Mempool(raw_by_hash={})
    deps = _RelayDeps(base, mempool)
    node = P2PService(
        listen_addrs=[tcp_multiaddr(port)],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / name / "p2p"),
    )
    await node.start()
    await asyncio.sleep(0.05)
    return node, mempool


@pytest.mark.asyncio
async def test_tx_relay_propagates_to_peer(tmp_path: Path) -> None:
    port_a = free_port()
    port_b = free_port()
    while port_b == port_a:
        port_b = free_port()
    node_a, mempool_a = await _start_node(tmp_path, "relay-a", port_a)
    node_b, mempool_b = await _start_node(tmp_path, "relay-b", port_b)
    try:
        await node_a.dial(tcp_multiaddr(port_b))
        def _connected() -> bool:
            if not node_a._peers or not node_b._peers:
                return False
            return all(p.hello_done.is_set() for p in node_a._peers.values()) and all(
                p.hello_done.is_set() for p in node_b._peers.values()
            )

        connected = await wait_for(_connected, timeout=8.0)
        assert connected, "Nodes did not connect in time"

        raw = b"tx:alice->bob:1"
        await node_a.relay_tx(raw)
        tx_hash = sha3_256(raw)

        seen = await wait_for(lambda: tx_hash in mempool_b.raw_by_hash, timeout=3.0)
        assert seen, "Relayed transaction was not admitted by peer"
        assert mempool_a.get_tx_raw(tx_hash) == raw
    finally:
        await node_a.stop()
        await node_b.stop()


@pytest.mark.asyncio
async def test_mempool_inv_on_connect_syncs_pending(tmp_path: Path) -> None:
    port_a = free_port()
    port_b = free_port()
    while port_b == port_a:
        port_b = free_port()
    node_a, mempool_a = await _start_node(tmp_path, "inv-a", port_a)
    node_b, mempool_b = await _start_node(tmp_path, "inv-b", port_b)
    try:
        raw = b"tx:preload"
        ok, _reason = mempool_a.admit_tx(raw)
        assert ok
        tx_hash = sha3_256(raw)

        await node_a.dial(tcp_multiaddr(port_b))
        def _connected() -> bool:
            if not node_a._peers or not node_b._peers:
                return False
            return all(p.hello_done.is_set() for p in node_a._peers.values()) and all(
                p.hello_done.is_set() for p in node_b._peers.values()
            )

        connected = await wait_for(_connected, timeout=8.0)
        assert connected, "Nodes did not connect in time"

        synced = await wait_for(lambda: tx_hash in mempool_b.raw_by_hash, timeout=3.0)
        assert synced, "Pending mempool tx was not synced on connect"
    finally:
        await node_a.stop()
        await node_b.stop()


@pytest.mark.asyncio
async def test_tx_relay_does_not_loop_back(tmp_path: Path) -> None:
    port_a = free_port()
    port_b = free_port()
    while port_b == port_a:
        port_b = free_port()
    node_a, _mempool_a = await _start_node(tmp_path, "loop-a", port_a)
    node_b, mempool_b = await _start_node(tmp_path, "loop-b", port_b)
    try:
        await node_a.dial(tcp_multiaddr(port_b))
        def _connected() -> bool:
            if not node_a._peers or not node_b._peers:
                return False
            return all(p.hello_done.is_set() for p in node_a._peers.values()) and all(
                p.hello_done.is_set() for p in node_b._peers.values()
            )

        connected = await wait_for(_connected, timeout=8.0)
        assert connected, "Nodes did not connect in time"

        raw = b"tx:loop-check"
        await node_a.relay_tx(raw)
        tx_hash = sha3_256(raw)
        synced = await wait_for(lambda: tx_hash in mempool_b.raw_by_hash, timeout=3.0)
        assert synced, "Relayed transaction was not admitted by peer"

        await asyncio.sleep(0.2)
        assert node_a._stats.get("tx_recv", 0) == 0, "Tx looped back to sender"
    finally:
        await node_a.stop()
        await node_b.stop()
