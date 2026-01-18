from __future__ import annotations

from p2p.node.p2p_service import P2PService


def test_advertise_respects_override(monkeypatch, tmp_path):
    monkeypatch.setenv("P2P_ADVERTISE_HOST", "203.0.113.5")
    monkeypatch.setenv("P2P_ADVERTISE_PORT", "30333")
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")

    service = P2PService(
        listen_addrs=["/ip4/0.0.0.0/tcp/30333"],
        chain_id=0,
        peerstore_path=str(tmp_path / "peerstore"),
    )

    advertised = service._advertised_addrs()
    assert any("203.0.113.5" in addr for addr in advertised)


def test_advertise_skips_loopback(monkeypatch, tmp_path):
    monkeypatch.setenv("P2P_ADVERTISE_HOST", "127.0.0.1")
    monkeypatch.setenv("P2P_ADVERTISE_PORT", "30333")
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")

    service = P2PService(
        listen_addrs=["/ip4/0.0.0.0/tcp/30333"],
        chain_id=0,
        peerstore_path=str(tmp_path / "peerstore"),
    )

    advertised = service._advertised_addrs()
    assert all("127.0.0.1" not in addr for addr in advertised)
