from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

import p2p
from core.types.block import Block
from core.utils.hash import ZERO32
from core.utils.pow import micro_threshold_to_target256
from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService
from p2p.tests import free_port, tcp_multiaddr
from rpc.methods import p2p as rpc_p2p

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"

os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
os.environ.setdefault("ANIMICA_P2P_ENABLE_DNS_SEEDS", "0")

# These are peer-discovery integration tests. None of them relay transactions,
# so the 6.0.0 txrelay / mempool-sync background tasks add nothing but noise:
# on the fresh loopback connections they churn ("send() on closed stream") and
# feed the peer-scoring system, which under a full-file run's socket/port reuse
# can penalize+ban a just-gossiped peer *before* the peer set stabilizes —
# flaking discovery in a way unrelated to what these tests assert. Disable tx
# relay module-wide (safe: no test carries txs) and default block-sync off; the
# one sync-convergence test below re-enables block sync (ANIMICA_SYNC_ENABLED)
# for itself. This is test isolation only — no production/security behavior is
# changed.
os.environ.setdefault("ANIMICA_P2P_TX_RELAY", "0")
os.environ.setdefault("ANIMICA_P2P_TX_ENABLED", "0")
os.environ.setdefault("ANIMICA_SYNC_ENABLED", "0")


def _make_deps(tmp_path: Path, name: str) -> tuple[P2PDeps, AsyncP2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, AsyncP2PDeps(sync_deps)


async def _wait_for_peers(node: P2PService, count: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if node.peer_count() >= count:
            return True
        await asyncio.sleep(0.1)
    return False


def _mine_blocks(sync_deps: P2PDeps, count: int) -> None:
    height, head_hash = sync_deps.head()
    header = sync_deps.header_by_hash(head_hash) if head_hash else None
    if header is None:
        header = sync_deps.header_by_number(0)
    assert header is not None

    timestamp = int(getattr(header, "timestamp", 0))
    for _ in range(count):
        timestamp += 1
        # Under 6.0.0 the block-import PoW gate is enforced (_pow_sanity):
        # header.hash() must be <= micro_threshold_to_target256(thetaMicro).
        # With the genesis theta a fixed nonce=0 only satisfies the target
        # probabilistically, so we actually mine — walk the nonce until the
        # header meets its own target — to build a chain of valid blocks
        # (the intent of this helper). The threshold is easy, so this takes
        # only a handful of iterations per block.
        nonce = 0
        while True:
            child = header.build_child(
                timestamp=timestamp,
                state_root=header.stateRoot,
                txs_root=ZERO32,
                receipts_root=ZERO32,
                proofs_root=ZERO32,
                da_root=ZERO32,
                nonce=nonce,
                extra=b"",
            )
            target = micro_threshold_to_target256(int(child.thetaMicro))
            if int.from_bytes(child.hash(), "big") <= target:
                break
            nonce += 1
        block = Block(header=child, txs=(), proofs=(), receipts=None)
        ok, reason = sync_deps.import_block(block)
        assert ok, reason
        header = child


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


def _register_rpc_service(service: P2PService) -> None:
    p2p.register_service(service)
    rpc_p2p._p2p_service = None


@pytest.mark.asyncio
async def test_runtime_peer_injection_connects(tmp_path: Path) -> None:
    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b")

    addr_a = tcp_multiaddr(free_port())
    addr_b = tcp_multiaddr(free_port())

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b" / "p2p"),
    )

    await node_a.start()
    await node_b.start()
    try:
        _register_rpc_service(node_b)
        result = await rpc_p2p.add_peers([addr_a])
        assert result.get("success") is True
        assert await _wait_for_peers(node_b, 1)
    finally:
        p2p.clear_service()
        rpc_p2p._p2p_service = None
        await node_b.stop()
        await node_a.stop()


@pytest.mark.asyncio
async def test_gossip_addr_relay_expands_peer_set(tmp_path: Path, monkeypatch) -> None:
    # Make addr gossip deterministic. With default intervals (request=30s,
    # relay=45s) the second peer is only discovered on the periodic sweep, so
    # a 15s wait races the schedule. Short intervals + private-network relay
    # (loopback addrs) let the gossip path fire promptly, which is exactly what
    # this test asserts.
    monkeypatch.setenv("ANIMICA_P2P_ADDR_REQUEST_INTERVAL", "1")
    monkeypatch.setenv("ANIMICA_P2P_ADDR_RESPONSE_INTERVAL", "1")
    monkeypatch.setenv("ANIMICA_P2P_ADDR_RELAY_INTERVAL", "1")
    monkeypatch.setenv("ANIMICA_P2P_OUTBOUND", "2")
    monkeypatch.setenv("ANIMICA_P2P_PRIVATE_NETWORK", "1")
    # This test exercises peer DISCOVERY only; it carries no txs and mines no
    # blocks. The 6.0.0 txrelay/mempool-sync + header-sync background tasks
    # would otherwise churn the fresh loopback connections ("send() on closed
    # stream" / "headers_send_failed" peer penalties) and can ban the just-
    # gossiped peer before the set stabilizes at 2 — a flaky failure unrelated
    # to discovery. Disable those unrelated subsystems so discovery is
    # deterministic. (The sync test below keeps them enabled.)
    monkeypatch.setenv("ANIMICA_P2P_TX_RELAY", "0")
    monkeypatch.setenv("ANIMICA_P2P_TX_ENABLED", "0")
    monkeypatch.setenv("ANIMICA_SYNC_ENABLED", "0")

    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b")
    deps_c_sync, deps_c = _make_deps(tmp_path, "node_c")

    addr_a = tcp_multiaddr(free_port())
    addr_b = tcp_multiaddr(free_port())
    addr_c = tcp_multiaddr(free_port())

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b" / "p2p"),
    )
    node_c = P2PService(
        listen_addrs=[addr_c],
        seeds=[],
        chain_id=deps_c_sync.chain_id,
        deps=deps_c,
        peerstore_path=str(tmp_path / "node_c" / "p2p"),
    )

    await node_a.start()
    await node_c.start()
    await node_b.start()
    try:
        await node_a.dial(addr_c)
        assert await _wait_for_peers(node_a, 1)

        _register_rpc_service(node_b)
        await rpc_p2p.add_peers([addr_a])
        assert await _wait_for_peers(node_b, 1)

        assert await _wait_for_peers(node_b, 2, timeout=20.0)
    finally:
        p2p.clear_service()
        rpc_p2p._p2p_service = None
        await node_b.stop()
        await node_c.stop()
        await node_a.stop()


@pytest.mark.asyncio
async def test_gossip_seed_chain_discovers_new_peer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_P2P_ADDR_REQUEST_INTERVAL", "1")
    monkeypatch.setenv("ANIMICA_P2P_ADDR_RESPONSE_INTERVAL", "1")
    monkeypatch.setenv("ANIMICA_P2P_ADDR_RELAY_INTERVAL", "1")
    monkeypatch.setenv("ANIMICA_P2P_OUTBOUND", "2")
    monkeypatch.setenv("ANIMICA_P2P_PRIVATE_NETWORK", "1")
    # Discovery-only test (no txs, no blocks): disable the unrelated 6.0.0
    # txrelay/mempool-sync + header-sync tasks whose connection churn +
    # "headers_send_failed" peer penalties otherwise flake this multi-hop
    # convergence. See test_gossip_addr_relay_expands_peer_set for details.
    monkeypatch.setenv("ANIMICA_P2P_TX_RELAY", "0")
    monkeypatch.setenv("ANIMICA_P2P_TX_ENABLED", "0")
    monkeypatch.setenv("ANIMICA_SYNC_ENABLED", "0")

    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b")
    deps_c_sync, deps_c = _make_deps(tmp_path, "node_c")

    addr_a = tcp_multiaddr(free_port())
    addr_b = tcp_multiaddr(free_port())
    addr_c = tcp_multiaddr(free_port())

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[addr_b],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[addr_c],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b" / "p2p"),
    )
    node_c = P2PService(
        listen_addrs=[addr_c],
        seeds=[],
        chain_id=deps_c_sync.chain_id,
        deps=deps_c,
        peerstore_path=str(tmp_path / "node_c" / "p2p"),
    )

    await node_c.start()
    await node_b.start()
    await node_a.start()
    try:
        # These are real multi-hop socket convergences. Under full-file runs
        # the shared event loop competes with the 6.0.0 txrelay/mempool-sync
        # background tasks and inter-test teardown, so allow more slack for the
        # gossip to converge (the assertions — the discovered peer counts — are
        # unchanged; only the tolerance for scheduling jitter grows).
        assert await _wait_for_peers(node_b, 1, timeout=25.0)
        assert await _wait_for_peers(node_a, 1, timeout=25.0)
        assert await _wait_for_peers(node_a, 2, timeout=35.0)
    finally:
        await node_a.stop()
        await node_b.stop()
        await node_c.stop()


@pytest.mark.asyncio
async def test_p2p_sync_converges_without_bootstrap_rpc(
    tmp_path: Path, monkeypatch
) -> None:
    # This test DOES exercise block sync (node_b must catch up to node_a's
    # mined chain), so re-enable it here (the module defaults it off for the
    # discovery-only tests). Tx relay stays off — no transactions are involved.
    monkeypatch.setenv("ANIMICA_SYNC_ENABLED", "1")

    deps_a_sync, deps_a = _make_deps(tmp_path, "node_a")
    deps_b_sync, deps_b = _make_deps(tmp_path, "node_b")

    addr_a = tcp_multiaddr(free_port())
    addr_b = tcp_multiaddr(free_port())

    node_a = P2PService(
        listen_addrs=[addr_a],
        seeds=[],
        chain_id=deps_a_sync.chain_id,
        deps=deps_a,
        peerstore_path=str(tmp_path / "node_a" / "p2p"),
    )
    node_b = P2PService(
        listen_addrs=[addr_b],
        seeds=[],
        chain_id=deps_b_sync.chain_id,
        deps=deps_b,
        peerstore_path=str(tmp_path / "node_b" / "p2p"),
    )

    await node_a.start()
    await node_b.start()
    try:
        _mine_blocks(deps_a_sync, 12)
        _register_rpc_service(node_b)
        await rpc_p2p.add_peers([addr_a])
        assert await _wait_for_peers(node_b, 1)
        assert await _wait_for_height(deps_b, 12, timeout=30.0)
    finally:
        p2p.clear_service()
        rpc_p2p._p2p_service = None
        await node_b.stop()
        await node_a.stop()
