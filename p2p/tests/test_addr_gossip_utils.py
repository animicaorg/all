from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from p2p.node.p2p_service import P2PService, _PeerState
from p2p.tests import free_port, tcp_multiaddr
from p2p.wire.frames import Framer


@pytest.mark.parametrize(
    "address,expected",
    [
        ("203.0.113.10:30333", "/ip4/203.0.113.10/tcp/30333"),
        ("/ip4/203.0.113.11/tcp/30333", "/ip4/203.0.113.11/tcp/30333"),
    ],
)
def test_sanitize_peer_addr_normalizes_public(address: str, expected: str, tmp_path: Path) -> None:
    svc = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=1,
        deps=None,
        peerstore_path=str(tmp_path / "p2p"),
    )
    sanitized = svc._sanitize_peer_addr(address, fallback_port=30333)
    assert sanitized == expected


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1:30333", "10.0.0.5:30333", "192.168.1.10:30333"],
)
def test_sanitize_peer_addr_filters_private(address: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_P2P_PRIVATE_NETWORK", "0")
    svc = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=1,
        deps=None,
        peerstore_path=str(tmp_path / "p2p"),
    )
    assert svc._sanitize_peer_addr(address, fallback_port=30333) is None


def test_sanitize_peer_addr_allows_private_when_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_P2P_PRIVATE_NETWORK", "1")
    svc = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=1,
        deps=None,
        peerstore_path=str(tmp_path / "p2p"),
    )
    assert svc._sanitize_peer_addr("127.0.0.1:30333", fallback_port=30333) == "/ip4/127.0.0.1/tcp/30333"


@pytest.mark.asyncio
async def test_peer_known_tracking_filters_samples(tmp_path: Path) -> None:
    svc = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=1,
        deps=None,
        peerstore_path=str(tmp_path / "p2p"),
    )
    addr_a = "/ip4/203.0.113.12/tcp/30333"
    addr_b = "/ip4/203.0.113.13/tcp/30333"
    svc._addrman.add(addr_a)
    svc._addrman.add(addr_b)

    peer = _PeerState(
        session_id="session",
        remote="203.0.113.12:30333",
        direction="outbound",
        conn=None,
        stream=None,
        framer=Framer(),
        write_lock=asyncio.Lock(),
    )
    svc._mark_peer_known(peer, addr_a)
    sample = svc._sample_addrs_for_peer(peer, limit=2)
    assert addr_a not in sample
    assert addr_b in sample
