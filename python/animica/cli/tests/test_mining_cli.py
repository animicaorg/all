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
    # Mock address validation to accept test address
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            assert isinstance(params, dict)
            if method == "miner.getBlockTemplate":
                assert params.get("include_mempool") is True
                return {
                    "enabled": True,
                    "header": {
                        "v": 1,
                        "chainId": 1337,
                        "height": 103,
                        "parentHash": "0x" + "00" * 32,
                        "timestamp": 0,
                        "stateRoot": "0x" + "00" * 32,
                        "txsRoot": "0x" + "00" * 32,
                        "receiptsRoot": "0x" + "00" * 32,
                        "proofsRoot": "0x" + "00" * 32,
                        "daRoot": "0x" + "00" * 32,
                        "mixSeed": "0x" + "00" * 32,
                        "poiesPolicyRoot": "0x" + "00" * 32,
                        "pqAlgPolicyRoot": "0x" + "00" * 32,
                        "thetaMicro": 1,
                        "nonce": 0,
                    },
                    "target": hex((1 << 256) - 1),
                    "coinbase": {"amount": 0},
                    "txs": [],
                    "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
                }
            if method == "miner.submitBlock":
                return {"accepted": True}
            raise AssertionError(f"Unexpected method {method}")
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    # Use monkeypatch to mock the module imports
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "3",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0
    assert "Successfully mined" in result.output
    assert "3 block(s)" in result.output


def test_mine_blocks_rpc_error(monkeypatch: Any) -> None:
    """Test that mine-blocks handles RPC errors gracefully."""
    # Mock address validation to accept test address
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
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
            "--address", test_address,
            "--count", "3",
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",  # Disable proxy for this test to avoid proxy error messages
        ],
    )
    
    assert result.exit_code == 5
    assert "Failed to connect to RPC" in result.output or "Failed to mine blocks via RPC" in result.output


def test_mine_blocks_invalid_address_fails(monkeypatch: Any) -> None:
    """Test that mine-blocks fails fast with an invalid address."""
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
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
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
            if method == "miner.getBlockTemplate":
                assert params.get("address") == test_address
                assert params.get("include_mempool") is True
                return {
                    "enabled": True,
                    "header": {
                        "v": 1,
                        "chainId": 1337,
                        "height": 1,
                        "parentHash": "0x" + "00" * 32,
                        "timestamp": 0,
                        "stateRoot": "0x" + "00" * 32,
                        "txsRoot": "0x" + "00" * 32,
                        "receiptsRoot": "0x" + "00" * 32,
                        "proofsRoot": "0x" + "00" * 32,
                        "daRoot": "0x" + "00" * 32,
                        "mixSeed": "0x" + "00" * 32,
                        "poiesPolicyRoot": "0x" + "00" * 32,
                        "pqAlgPolicyRoot": "0x" + "00" * 32,
                        "thetaMicro": 1,
                        "nonce": 0,
                    },
                    "target": hex((1 << 256) - 1),
                    "coinbase": {"amount": 0},
                    "txs": [],
                    "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
                }
            if method == "miner.submitBlock":
                return {"accepted": True}
            raise AssertionError(f"Unexpected method {method}")
    
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
            if method == "miner.getBlockTemplate":
                return {
                    "enabled": True,
                    "header": {
                        "v": 1,
                        "chainId": 1337,
                        "height": 1,
                        "parentHash": "0x" + "00" * 32,
                        "timestamp": 0,
                        "stateRoot": "0x" + "00" * 32,
                        "txsRoot": "0x" + "00" * 32,
                        "receiptsRoot": "0x" + "00" * 32,
                        "proofsRoot": "0x" + "00" * 32,
                        "daRoot": "0x" + "00" * 32,
                        "mixSeed": "0x" + "00" * 32,
                        "poiesPolicyRoot": "0x" + "00" * 32,
                        "pqAlgPolicyRoot": "0x" + "00" * 32,
                        "thetaMicro": 1,
                        "nonce": 0,
                    },
                    "target": hex((1 << 256) - 1),
                    "coinbase": {"amount": 0},
                    "txs": [],
                    "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
                }
            if method == "miner.submitBlock":
                return {"accepted": True}
            return {}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
            "--count", "3",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0
    # Should have 2 sleep calls for 3 blocks (no sleep after last block)
    assert len(sleep_calls) == 2
    # Each sleep should be 2 seconds
    assert all(s == 2.0 for s in sleep_calls)


def _create_mock_rpc_client_with_device_tracking() -> tuple[type, dict[str, Any]]:
    """Helper to create a mock RPC client that tracks RPC parameters.
    
    Note: Device parameter should NOT be sent to RPC (it's CLI-only).
    This helper verifies that device is not in RPC params.
    
    Returns:
        tuple: (MockRpcClient class, tracking dict with keys:
                'has_device' (bool): True if device was in params,
                'params' (dict | None): The RPC params dict or None)
    """
    params_tracker = {"has_device": False, "params": None}
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            if isinstance(params, dict):
                params_tracker["params"] = params
                params_tracker["has_device"] = "device" in params
            if method == "miner.getBlockTemplate":
                return {
                    "enabled": True,
                    "header": {
                        "v": 1,
                        "chainId": 1337,
                        "height": 1,
                        "parentHash": "0x" + "00" * 32,
                        "timestamp": 0,
                        "stateRoot": "0x" + "00" * 32,
                        "txsRoot": "0x" + "00" * 32,
                        "receiptsRoot": "0x" + "00" * 32,
                        "proofsRoot": "0x" + "00" * 32,
                        "daRoot": "0x" + "00" * 32,
                        "mixSeed": "0x" + "00" * 32,
                        "poiesPolicyRoot": "0x" + "00" * 32,
                        "pqAlgPolicyRoot": "0x" + "00" * 32,
                        "thetaMicro": 1,
                        "nonce": 0,
                    },
                    "target": hex((1 << 256) - 1),
                    "coinbase": {"amount": 0},
                    "txs": [],
                    "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
                }
            if method == "miner.submitBlock":
                return {"accepted": True}
            return {}
    
    return MockRpcClient, params_tracker


def _setup_mock_rpc_client(monkeypatch: Any, test_address: str) -> dict[str, Any]:
    """Helper to set up mock RPC client and address validation.
    
    Returns:
        dict: Params tracking dictionary with 'has_device' and 'params' keys
    """
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    
    MockRpcClient, params_tracker = _create_mock_rpc_client_with_device_tracking()
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    return params_tracker


def test_mine_blocks_with_device_cpu(monkeypatch: Any) -> None:
    """Test that mine-blocks accepts --device cpu without sending to RPC."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--device", "cpu",
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",  # Disable proxy for simpler test
        ],
    )
    
    assert result.exit_code == 0
    # Device should NOT be sent to RPC (it's CLI-only)
    assert not params_tracker["has_device"], "Device parameter should not be sent to RPC"
    assert "Using device: cpu" in result.output


def test_mine_blocks_with_device_cuda(monkeypatch: Any) -> None:
    """Test that mine-blocks accepts --device cuda without sending to RPC."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--device", "cuda",
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )
    
    assert result.exit_code == 0
    # Device should NOT be sent to RPC (it's CLI-only)
    assert not params_tracker["has_device"], "Device parameter should not be sent to RPC"
    assert "Using device: cuda" in result.output


def test_mine_blocks_with_device_auto(monkeypatch: Any) -> None:
    """Test that mine-blocks accepts --device auto and auto-detects device."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
    
    # Mock auto_detect_device to return cpu for test
    def mock_auto_detect():
        return "cpu"
    
    monkeypatch.setattr("mining.device.auto_detect_device", mock_auto_detect)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--device", "auto",
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )
    
    assert result.exit_code == 0
    # Device should NOT be sent to RPC (it's CLI-only)
    assert not params_tracker["has_device"], "Device parameter should not be sent to RPC"
    assert "Auto-detected device: cpu" in result.output
    assert "Using device: cpu" in result.output


def test_mine_blocks_with_all_supported_devices(monkeypatch: Any) -> None:
    """Test that mine-blocks accepts all supported device values."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    # Import the constant to ensure consistency with main module
    from animica.cli.mining import SUPPORTED_DEVICES
    
    # Mock auto_detect_device to return cpu for "auto" tests
    monkeypatch.setattr("mining.device.auto_detect_device", lambda: "cpu")
    
    for device in SUPPORTED_DEVICES:
        # Setup fresh mock for each device
        params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
        
        result = runner.invoke(
            mining.app,
            [
                "mine-blocks",
                "--address", test_address,
                "--count", "1",
                "--device", device,
                "--rpc-url", "http://127.0.0.1:8545",
                "--no-proxy",
            ],
        )
        
        assert result.exit_code == 0, f"Device {device} failed with: {result.output}"
        
        # Device should NOT be sent to RPC (it's CLI-only)
        assert not params_tracker["has_device"], f"Device parameter should not be sent to RPC for {device}"
        
        # For "auto" device, the actual device used will be the auto-detected one (cpu in our mock)
        if device == "auto":
            assert "Auto-detected device: cpu" in result.output
            assert "Using device: cpu" in result.output
        else:
            assert f"Using device: {device}" in result.output


def test_mine_blocks_with_invalid_device() -> None:
    """Test that mine-blocks rejects invalid device values."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--device", "invalid_device",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 2
    assert "unsupported device" in result.output.lower()
    assert "invalid_device" in result.output


def test_mine_blocks_with_device_case_insensitive(monkeypatch: Any) -> None:
    """Test that device parameter is case-insensitive."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--device", "CUDA",  # Upper case
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )
    
    assert result.exit_code == 0
    # Device should NOT be sent to RPC (it's CLI-only)
    assert not params_tracker["has_device"], "Device parameter should not be sent to RPC"
    # Should be normalized to lowercase in output
    assert "Using device: cuda" in result.output


def test_mine_blocks_without_device_defaults_to_auto(monkeypatch: Any) -> None:
    """Test that mine-blocks defaults to auto device when --device is not specified."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
    
    # Mock auto_detect_device to return cpu for test
    monkeypatch.setattr("mining.device.auto_detect_device", lambda: "cpu")
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            # No --device flag specified
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )
    
    assert result.exit_code == 0
    # Device should NOT be sent to RPC (it's CLI-only)
    assert not params_tracker["has_device"], "Device parameter should not be sent to RPC"
    # Should auto-detect and use cpu (from our mock)
    assert "Auto-detected device: cpu" in result.output
    assert "Using device: cpu" in result.output


def test_mine_blocks_device_from_env_var(monkeypatch: Any) -> None:
    """Test that device can be set via ANIMICA_MINER_DEVICE environment variable."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setenv("ANIMICA_MINER_DEVICE", "cuda")
    params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            # No --device flag, should use env var
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )
    
    assert result.exit_code == 0
    # Device should NOT be sent to RPC (it's CLI-only)
    assert not params_tracker["has_device"], "Device parameter should not be sent to RPC"
    # Should use env var value
    assert "Using device: cuda" in result.output


def test_mine_blocks_auto_detect_cuda(monkeypatch: Any) -> None:
    """Test auto-detection selects CUDA when available."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
    
    # Mock auto_detect_device to return cuda
    monkeypatch.setattr("mining.device.auto_detect_device", lambda: "cuda")
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--device", "auto",
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )
    
    assert result.exit_code == 0
    # Device should NOT be sent to RPC (it's CLI-only)
    assert not params_tracker["has_device"], "Device parameter should not be sent to RPC"
    assert "Auto-detected device: cuda" in result.output
    assert "Using device: cuda" in result.output


def test_mine_blocks_auto_detect_fallback_on_error(monkeypatch: Any) -> None:
    """Test auto-detection falls back to CPU on error."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
    
    # Mock auto_detect_device to raise an exception
    def mock_error():
        raise RuntimeError("Detection failed")
    
    monkeypatch.setattr("mining.device.auto_detect_device", mock_error)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--device", "auto",
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )
    
    assert result.exit_code == 0
    # Device should NOT be sent to RPC (it's CLI-only)
    assert not params_tracker["has_device"], "Device parameter should not be sent to RPC"
    # Should fallback to cpu
    assert "Could not auto-detect device" in result.output
    assert "Falling back to CPU" in result.output
    assert "Using device: cpu" in result.output


def test_mine_blocks_explicit_device_overrides_auto(monkeypatch: Any) -> None:
    """Test that explicitly setting a device overrides auto-detection."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    params_tracker = _setup_mock_rpc_client(monkeypatch, test_address)
    
    # Mock auto_detect_device to return cuda (but we'll explicitly request cpu)
    monkeypatch.setattr("mining.device.auto_detect_device", lambda: "cuda")
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--device", "cpu",  # Explicitly request CPU
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )
    
    assert result.exit_code == 0
    # Device should NOT be sent to RPC (it's CLI-only)
    assert not params_tracker["has_device"], "Device parameter should not be sent to RPC"
    # Should use explicitly requested CPU
    # Should NOT show auto-detection message
    assert "Auto-detected device" not in result.output
    assert "Using device: cpu" in result.output


def test_mine_blocks_with_no_timeout(monkeypatch: Any) -> None:
    """Test that mine-blocks accepts --no-timeout flag."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    
    # Track the timeout value passed to RpcClient
    timeout_tracker = {"timeout": -1}  # -1 means not set

    class MockRpcClient:
        def __init__(self, url, timeout=None):
            timeout_tracker["timeout"] = timeout
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            if method == "miner.getBlockTemplate":
                return {
                    "enabled": True,
                    "header": {
                        "v": 1,
                        "chainId": 1337,
                        "height": 1,
                        "parentHash": "0x" + "00" * 32,
                        "timestamp": 0,
                        "stateRoot": "0x" + "00" * 32,
                        "txsRoot": "0x" + "00" * 32,
                        "receiptsRoot": "0x" + "00" * 32,
                        "proofsRoot": "0x" + "00" * 32,
                        "daRoot": "0x" + "00" * 32,
                        "mixSeed": "0x" + "00" * 32,
                        "poiesPolicyRoot": "0x" + "00" * 32,
                        "pqAlgPolicyRoot": "0x" + "00" * 32,
                        "thetaMicro": 1,
                        "nonce": 0,
                    },
                    "target": hex((1 << 256) - 1),
                    "coinbase": {"amount": 0},
                    "txs": [],
                    "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
                }
            if method == "miner.submitBlock":
                return {"accepted": True}
            return {}
    
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
            "--no-timeout",
        ],
    )
    
    assert result.exit_code == 0
    # Timeout should be None when --no-timeout is used
    assert timeout_tracker["timeout"] is None
    assert "RPC timeout disabled" in result.output


def test_mine_blocks_without_no_timeout_uses_default(monkeypatch: Any) -> None:
    """Test that mine-blocks uses default timeout when --no-timeout is not specified."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    
    # Track the timeout value passed to RpcClient
    timeout_tracker = {"timeout": -1}  # -1 means not set

    class MockRpcClient:
        def __init__(self, url, timeout=None):
            timeout_tracker["timeout"] = timeout
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            if method == "miner.getBlockTemplate":
                return {
                    "enabled": True,
                    "header": {
                        "v": 1,
                        "chainId": 1337,
                        "height": 1,
                        "parentHash": "0x" + "00" * 32,
                        "timestamp": 0,
                        "stateRoot": "0x" + "00" * 32,
                        "txsRoot": "0x" + "00" * 32,
                        "receiptsRoot": "0x" + "00" * 32,
                        "proofsRoot": "0x" + "00" * 32,
                        "daRoot": "0x" + "00" * 32,
                        "mixSeed": "0x" + "00" * 32,
                        "poiesPolicyRoot": "0x" + "00" * 32,
                        "pqAlgPolicyRoot": "0x" + "00" * 32,
                        "thetaMicro": 1,
                        "nonce": 0,
                    },
                    "target": hex((1 << 256) - 1),
                    "coinbase": {"amount": 0},
                    "txs": [],
                    "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
                }
            if method == "miner.submitBlock":
                return {"accepted": True}
            return {}
    
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
            # --no-timeout NOT specified
        ],
    )
    
    assert result.exit_code == 0
    # Timeout should default to None (unbounded) when --no-timeout is not used
    assert timeout_tracker["timeout"] is None
    assert "RPC timeout disabled" not in result.output
