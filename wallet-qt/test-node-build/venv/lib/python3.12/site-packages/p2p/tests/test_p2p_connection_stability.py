import asyncio
import socket
from contextlib import closing

import pytest

from p2p.node.p2p_service import P2PService


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _eventually(predicate, timeout=10.0, interval=0.1) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
async def test_two_node_outbound_stays_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    port_a = _free_port()
    port_b = _free_port()

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

    zero_hash = "0x" + "00" * 32
    monkeypatch.setattr(node_a, "_local_head", lambda: (0, zero_hash))
    monkeypatch.setattr(node_b, "_local_head", lambda: (0, zero_hash))
    monkeypatch.setattr(node_a, "_genesis_hash", lambda: b"\x00" * 32)
    monkeypatch.setattr(node_b, "_genesis_hash", lambda: b"\x00" * 32)

    async def _noop_dial_loop() -> None:
        while node_a._running:  # pylint: disable=protected-access
            await asyncio.sleep(0.5)

    monkeypatch.setattr(node_a, "_dial_loop", _noop_dial_loop)

    await node_a.start()
    await node_b.start()

    try:
        async def _have_outbound_peer() -> bool:
            outbound = [
                p
                for p in node_b.peer_registry.snapshot()
                if p.get("direction") == "outbound" and p.get("peer_id") not in (None, "unknown")
            ]
            return bool(outbound)

        connected = await _eventually(_have_outbound_peer, timeout=15.0)
        assert connected, "Outbound peer did not connect in time"

        await asyncio.sleep(2.0)
        outbound = [
            p
            for p in node_b.peer_registry.snapshot()
            if p.get("direction") == "outbound" and p.get("peer_id") not in (None, "unknown")
        ]
        assert outbound, "Outbound peer disconnected unexpectedly"

        peer_id = outbound[0].get("peer_id")
        assert peer_id
        stored = node_b.peerstore.get(peer_id)
        assert stored is not None
        assert stored.last_disconnect_reason is None
    finally:
        await node_b.stop()
        await node_a.stop()
