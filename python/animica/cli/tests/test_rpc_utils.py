from __future__ import annotations

import pytest

from animica.cli import rpc_utils


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8545/rpc", True),
        ("http://localhost:8545/rpc", True),
        ("http://[::1]:8545/rpc", True),
        ("http://0.0.0.0:8545/rpc", True),
        ("127.0.0.1:8545/rpc", True),
        ("http://127.0.0.1:8545/rpc", False),
        ("http://192.168.1.10:8545/rpc", False),
    ],
)
def test_is_local_rpc_url(url: str, expected: bool) -> None:
    assert rpc_utils.is_local_rpc_url(url) is expected


def test_candidate_rpc_urls_adds_container_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_DOCKER_HOST", "172.20.0.1")
    monkeypatch.setattr(rpc_utils, "_running_in_container", lambda: True)
    monkeypatch.setattr(rpc_utils, "_default_gateway_ip", lambda: "172.18.0.1")

    candidates = rpc_utils.candidate_rpc_urls("http://127.0.0.1:8545/rpc")

    assert "http://127.0.0.1:8545/rpc" in candidates
    assert "http://172.20.0.1:8545/rpc" in candidates
    assert "http://host.docker.internal:8545/rpc" in candidates
    assert "http://gateway.docker.internal:8545/rpc" in candidates
    assert "http://172.18.0.1:8545/rpc" in candidates


def test_candidate_rpc_urls_ignores_container_hosts_when_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_DOCKER_HOST", "172.20.0.1")
    monkeypatch.setattr(rpc_utils, "_running_in_container", lambda: False)

    candidates = rpc_utils.candidate_rpc_urls("http://127.0.0.1:8545/rpc")

    assert "http://127.0.0.1:8545/rpc" in candidates
    assert "http://172.20.0.1:8545/rpc" not in candidates
