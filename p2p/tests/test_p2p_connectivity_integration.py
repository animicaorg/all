from __future__ import annotations

import asyncio
import socket
import time

import pytest

from p2p.node.p2p_service import P2PService


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for_peer(service: P2PService, *, timeout_s: float = 30.0) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        snapshot = service.status_snapshot()
        if snapshot.peers_total >= 1:
            return
        await asyncio.sleep(0.5)
    raise AssertionError("Peers did not connect within timeout")


@pytest.mark.asyncio
async def test_two_nodes_connect(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    monkeypatch.setenv("ANIMICA_P2P_EXTERNAL_IP", "203.0.113.5")

    port_a = _free_port()
    port_b = _free_port()

    service_a = P2PService(
        listen_addrs=[f"/ip4/127.0.0.1/tcp/{port_a}"],
        seeds=[f"127.0.0.1:{port_b}"],
        chain_id=0,
        peerstore_path=str(tmp_path / "node-a"),
    )
    service_b = P2PService(
        listen_addrs=[f"/ip4/127.0.0.1/tcp/{port_b}"],
        seeds=[f"127.0.0.1:{port_a}"],
        chain_id=0,
        peerstore_path=str(tmp_path / "node-b"),
    )

    await service_a.start()
    await service_b.start()
    try:
        await _wait_for_peer(service_a)
        await _wait_for_peer(service_b)
    finally:
        await service_a.stop()
        await service_b.stop()
