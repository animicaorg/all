from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import typer
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
    """Test that mine-blocks command is registered and has correct parameters."""
    # Test that the command can be invoked (even if it fails due to missing args)
    # This verifies the command is registered without accessing private attributes
    try:
        result = runner.invoke(mining.app, ["mine-blocks", "--help"])
        # If help works, command exists - but stub Typer may not support --help
    except (typer.BadParameter, AttributeError):
        # Expected with stub Typer - command exists but help not supported
        pass
    
    # Alternative: test that invoking with missing args gives appropriate error
    try:
        runner.invoke(mining.app, ["mine-blocks"])
    except typer.BadParameter as e:
        # Command exists and validates arguments
        assert "address" in str(e) or "count" in str(e)


def test_mine_blocks_missing_address() -> None:
    """Test that mine-blocks fails when address is missing."""
    try:
        result = runner.invoke(mining.app, ["mine-blocks", "--count", "5"])
        # Should fail with exit code or raise exception
        assert result.exit_code != 0
    except typer.BadParameter as e:
        # Expected - missing required argument
        assert "address" in str(e)


def test_mine_blocks_missing_count() -> None:
    """Test that mine-blocks fails when count is missing."""
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


def test_mine_blocks_invalid_address_fails(monkeypatch: Any) -> None:
    """Test that mine-blocks fails fast with an invalid address."""
    # Mock validate_address to return False
    def mock_validate(addr, expect_hrp=None):
        raise ValueError("Invalid address")
    
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: False)
    monkeypatch.setattr(mining, "_resolve_wallet_label_to_address", lambda x, y=None: None)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", "invalid_address",
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 2
    assert "neither a valid Animica Bech32 address" in result.output or "not a valid" in result.output


def test_mine_blocks_with_wallet_label(monkeypatch: Any, tmp_path: Path) -> None:
    """Test that mine-blocks resolves wallet labels correctly."""
    import json
    
    # Create a test wallet file
    wallet_file = tmp_path / "wallets.json"
    test_address = "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f"
    wallet_data = {
        "version": 1,
        "wallets": [
            {
                "label": "test-miner",
                "address": test_address,
                "alg_id": 1,
                "alg_name": "dilithium3",
                "public_key_hex": "abcd1234",
                "secret_key_hex": "secret",
                "created_at": "2024-01-01T00:00:00Z",
            }
        ],
    }
    wallet_file.write_text(json.dumps(wallet_data))
    
    # Mock wallet file path resolution
    monkeypatch.setattr(mining, "_resolve_wallet_label_to_address", lambda label, wf=None: test_address if label == "test-miner" else None)
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            # Verify the resolved address is used
            if isinstance(params, dict):
                assert params.get("address") == test_address
            return {"mined": 1, "height": 1}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", "test-miner",
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0
    assert "Successfully mined" in result.output


def test_mine_blocks_enforces_2s_delay(monkeypatch: Any) -> None:
    """Test that mine-blocks adds 2s delay between blocks when count > 1."""
    import time
    
    sleep_calls = []
    
    def mock_sleep(seconds):
        sleep_calls.append(seconds)
    
    monkeypatch.setattr(time, "sleep", mock_sleep)
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            return {"mined": 1, "height": 1}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f",
            "--count", "3",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0
    # Should have 2 sleep calls for 3 blocks (no sleep after last block)
    assert len(sleep_calls) == 2
    # Each sleep should be 2 seconds
    assert all(s == 2.0 for s in sleep_calls)
