from __future__ import annotations

from p2p.node.p2p_service import P2PService


def test_seed_normalization_accepts_host_port(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    service = P2PService(chain_id=0, peerstore_path=str(tmp_path / "peerstore"))
    normalized = service._normalize_seed("3.133.122.91:30333")
    assert normalized == "tcp://3.133.122.91:30333"


def test_seed_normalization_accepts_tcp_scheme(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    service = P2PService(chain_id=0, peerstore_path=str(tmp_path / "peerstore"))
    normalized = service._normalize_seed("tcp://3.133.122.91:30333")
    assert normalized == "tcp://3.133.122.91:30333"
