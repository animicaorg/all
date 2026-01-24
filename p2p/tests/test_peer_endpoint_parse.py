from __future__ import annotations

import pytest

from p2p.peer.peer_addr import parse_peer_endpoint


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/ip4/3.12.224.189/tcp/30333", ("tcp", "3.12.224.189", 30333)),
        ("/dns4/mainnet.animica.org/tcp/30333", ("tcp", "mainnet.animica.org", 30333)),
        ("tcp://3.12.224.189:30333", ("tcp", "3.12.224.189", 30333)),
        ("tcp://144.126.133.21:30333", ("tcp", "144.126.133.21", 30333)),
    ],
)
def test_parse_peer_endpoint_accepts_seed_formats(raw: str, expected: tuple[str, str, int]) -> None:
    endpoint = parse_peer_endpoint(raw, allow_quic=False, allow_ws=False, allow_tcp=True)
    assert (endpoint.scheme, endpoint.host, endpoint.port) == expected

