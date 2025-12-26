from __future__ import annotations

import os

from animica.cli import mining as mining_cli
from mining.config import MiningConfig


def test_mining_cli_defaults_to_local_rpc(monkeypatch) -> None:
    monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
    monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
    mining_cli._ensure_network_env()
    rpc_url = os.environ.get("ANIMICA_RPC_URL", "")
    assert rpc_url.startswith("http://127.0.0.1")
    assert "rpc.animica.org" not in rpc_url


def test_mining_config_respects_env_rpc(monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_MINER_RPC_HTTP", "http://localhost:9999/rpc")
    cfg = MiningConfig.from_env()
    assert cfg.rpc_http_url == "http://localhost:9999/rpc"
