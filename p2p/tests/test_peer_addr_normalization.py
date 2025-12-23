from __future__ import annotations

import pytest

from p2p.peer.peer_addr import normalize_peer_addr


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("tcp://example.com:30333", "tcp://example.com:30333"),
        ("p2p://example.com:30333", "tcp://example.com:30333"),
        ("/ip4/144.126.133.21/tcp/30333", "tcp://144.126.133.21:30333"),
        ("/dns4/mainnet.animica.org/tcp/30333", "tcp://mainnet.animica.org:30333"),
        (
            "/dns4/mainnet.animica.org/tcp/30333/p2p/12D3KooWXYZ",
            "tcp://mainnet.animica.org:30333",
        ),
        ("203.0.113.10:30333", "tcp://203.0.113.10:30333"),
    ],
)
def test_normalize_peer_addr_tcp(raw: str, expected: str) -> None:
    parsed = normalize_peer_addr(raw)
    assert parsed.addr is not None
    assert parsed.addr.canonical == expected


def test_normalize_peer_addr_ws_multiaddr() -> None:
    parsed = normalize_peer_addr("/dns4/ws.animica.org/tcp/443/ws", allow_ws=True)
    assert parsed.addr is not None
    assert parsed.addr.canonical == "ws://ws.animica.org:443"


def test_normalize_peer_addr_ws_rejected() -> None:
    parsed = normalize_peer_addr("/dns4/ws.animica.org/tcp/443/ws", allow_ws=False)
    assert parsed.addr is None
    assert parsed.reason == "unsupported_ws"


def test_normalize_peer_addr_quic_multiaddr() -> None:
    parsed = normalize_peer_addr("/ip4/144.126.133.21/udp/443/quic-v1", allow_quic=True)
    assert parsed.addr is not None
    assert parsed.addr.canonical == "quic://144.126.133.21:443"


def test_normalize_peer_addr_quic_rejected() -> None:
    parsed = normalize_peer_addr("/ip4/144.126.133.21/udp/443/quic-v1", allow_quic=False)
    assert parsed.addr is None
    assert parsed.reason == "unsupported_quic"


def test_normalize_peer_addr_unknown_scheme() -> None:
    parsed = normalize_peer_addr("udp://example.com:1234")
    assert parsed.addr is None
    assert parsed.reason == "unsupported_scheme:udp"
