from __future__ import annotations

from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from animica.cli import mining

runner = CliRunner()


def test_show_config(monkeypatch: Any) -> None:
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://rpc")
    monkeypatch.setenv("ANIMICA_MINING_POOL_DB_URL", "sqlite:///db")
    monkeypatch.setenv("ANIMICA_STRATUM_BIND", "0.0.0.0:3333")
    result = runner.invoke(mining.app, ["show-config"])
    assert result.exit_code == 0
    assert "RPC URL" in result.output


def test_run_pool_sets_env(monkeypatch: Any) -> None:
    called = {}

    def fake_main(argv: list[str] | None = None) -> None:
        called["argv"] = argv

    monkeypatch.setattr(mining.pool_cli, "main", fake_main)
    result = runner.invoke(
        mining.app,
        [
            "run-pool",
            "--rpc-url",
            "http://node",
            "--db-url",
            "sqlite:///db",
            "--stratum-bind",
            "0.0.0.0:3333",
            "--api-bind",
            "0.0.0.0:8082",
            "--log-level",
            "debug",
        ],
    )
    assert result.exit_code == 0
    assert called["argv"] == []
    import os

    assert os.getenv("ANIMICA_RPC_URL") == "http://node"
    assert os.getenv("ANIMICA_MINING_POOL_DB_URL") == "sqlite:///db"
    assert os.getenv("ANIMICA_STRATUM_BIND") == "0.0.0.0:3333"
    assert os.getenv("ANIMICA_POOL_API_BIND") == "0.0.0.0:8082"
    assert os.getenv("ANIMICA_MINING_POOL_LOG_LEVEL") == "debug"
    for key in [
        "ANIMICA_RPC_URL",
        "ANIMICA_MINING_POOL_DB_URL",
        "ANIMICA_STRATUM_BIND",
        "ANIMICA_POOL_API_BIND",
        "ANIMICA_MINING_POOL_LOG_LEVEL",
    ]:
        os.environ.pop(key, None)


def test_generate_payout_address(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    result = runner.invoke(
        mining.app,
        [
            "generate-payout-address",
            "--wallet-file",
            str(wallet_file),
            "--label",
            "pool-payout",
        ],
    )
    assert result.exit_code == 0
    assert "pool-payout" in result.output
    assert wallet_file.exists()
