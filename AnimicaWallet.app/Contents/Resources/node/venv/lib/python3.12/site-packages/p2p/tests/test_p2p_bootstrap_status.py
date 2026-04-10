import asyncio
import socket
from contextlib import closing

import pytest

import p2p
from p2p.node.p2p_service import P2PService
from rpc.methods import p2p as rpc_p2p


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def eventually(predicate, timeout=20.0, interval=0.1) -> bool:
    end = asyncio.get_event_loop().time() + timeout
    while True:
        ok = await predicate() if asyncio.iscoroutinefunction(predicate) else predicate()
        if ok:
            return True
        if asyncio.get_event_loop().time() >= end:
            return False
        await asyncio.sleep(interval)


def _count_direction(service: P2PService, direction: str) -> int:
    snapshot = service.peer_registry.snapshot()
    return sum(1 for peer in snapshot if peer.get("direction") == direction)


@pytest.mark.asyncio
async def test_p2p_bootstrap_and_status_reflects_peers(tmp_path):
    port_a = find_free_port()
    port_b = find_free_port()

    node_a = P2PService(
        listen_addrs=[f"/ip4/127.0.0.1/tcp/{port_a}"],
        seeds=[],
        chain_id=1337,
        peerstore_path=tmp_path / "node-a",
    )
    node_b = P2PService(
        listen_addrs=[f"/ip4/127.0.0.1/tcp/{port_b}"],
        seeds=[f"/ip4/127.0.0.1/tcp/{port_a}"],
        chain_id=1337,
        peerstore_path=tmp_path / "node-b",
    )

    await node_a.start()
    await node_b.start()
    p2p.register_service(node_b)

    try:
        ok = await eventually(
            lambda: _count_direction(node_a, "inbound") >= 1
            and _count_direction(node_b, "outbound") >= 1,
            timeout=30.0,
        )
        assert ok, (
            "Peers failed to connect within timeout: "
            f"A_in={_count_direction(node_a, 'inbound')} "
            f"B_out={_count_direction(node_b, 'outbound')}"
        )

        snap_b = node_b.status_snapshot()
        assert snap_b.p2p_running is True
        assert snap_b.peers_total >= 1
        assert snap_b.peers_outbound >= 1
        assert snap_b.bootstrap_attempts_last_5m >= 1
        assert snap_b.last_peer_connect_at is not None
        assert "config" in snap_b.seed_sources

        rpc_status = await rpc_p2p.get_status()
        assert rpc_status["p2p_running"] is True
        assert rpc_status["peers_total"] >= 1
        assert rpc_status["peers_outbound"] >= 1
    finally:
        p2p.clear_service()
        await node_b.stop()
        await node_a.stop()
