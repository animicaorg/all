from __future__ import annotations

from animica.config import get_chain_data_dir


def test_chain_data_dir_prefers_animica_data_dir(monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_DATA_DIR", "/data")
    data_dir = get_chain_data_dir(0)
    assert str(data_dir) == "/data/chain-0"
    assert "/root" not in str(data_dir)


def test_chain_data_dir_defaults_to_docker_path(monkeypatch) -> None:
    monkeypatch.delenv("ANIMICA_DATA_DIR", raising=False)
    monkeypatch.setenv("ANIMICA_DOCKER", "1")
    data_dir = get_chain_data_dir(0)
    assert str(data_dir) == "/data/chain-0"
