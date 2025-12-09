from __future__ import annotations

from pathlib import Path
from typing import Any

from animica.cli import mining
from typer.testing import CliRunner

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


def test_mine_blocks_command_exists() -> None:
    """Test that mine-blocks command is registered."""
    # Check that the command is registered in the app
    assert "mine-blocks" in mining.app._commands
    
    # Verify the command has the expected parameters
    cmd = mining.app._commands["mine-blocks"]
    import inspect
    sig = inspect.signature(cmd)
    assert "address" in sig.parameters
    assert "count" in sig.parameters
    assert "rpc_url" in sig.parameters


def test_mine_blocks_missing_address() -> None:
    """Test that mine-blocks fails when address is missing."""
    import typer
    try:
        result = runner.invoke(mining.app, ["mine-blocks", "--count", "5"])
        # Should fail with exit code or raise exception
        assert result.exit_code != 0
    except typer.BadParameter as e:
        # Expected - missing required argument
        assert "address" in str(e)


def test_mine_blocks_missing_count() -> None:
    """Test that mine-blocks fails when count is missing."""
    import typer
    try:
        result = runner.invoke(mining.app, ["mine-blocks", "--address", "anim1test123"])
        # Should fail with exit code or raise exception
        assert result.exit_code != 0
    except typer.BadParameter as e:
        # Expected - missing required argument
        assert "count" in str(e)


def test_mine_blocks_invalid_count_zero() -> None:
    """Test that count=0 is rejected."""
    result = runner.invoke(
        mining.app,
        ["mine-blocks", "--address", "anim1test123", "--count", "0"],
    )
    assert result.exit_code == 2
    assert "must be greater than 0" in result.output


def test_mine_blocks_invalid_count_negative() -> None:
    """Test that negative count is rejected."""
    result = runner.invoke(
        mining.app,
        ["mine-blocks", "--address", "anim1test123", "--count", "-5"],
    )
    assert result.exit_code == 2
    assert "must be greater than 0" in result.output


def test_mine_blocks_success(monkeypatch: Any) -> None:
    """Test that mine-blocks calls RPC successfully."""
    from unittest.mock import Mock
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: list):
            return {"mined": 3, "height": 103}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    # Use monkeypatch to mock the module imports
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", "anim1test123",
            "--count", "3",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0
    assert "Successfully mined" in result.output
    assert "3 block(s)" in result.output


def test_mine_blocks_rpc_error(monkeypatch: Any) -> None:
    """Test that mine-blocks handles RPC errors gracefully."""
    from unittest.mock import Mock
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: list):
            raise ConnectionError("RPC connection failed")
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    # Use monkeypatch to mock the module imports
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", "anim1test123",
            "--count", "3",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 5
    assert "Failed to connect to RPC" in result.output
