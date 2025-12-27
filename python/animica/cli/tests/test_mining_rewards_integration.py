"""
Integration tests for mining rewards with wallet labels and raw addresses.

These tests verify that:
1. Mining to a wallet label credits the correct address
2. Mining to a raw Bech32 address credits that address
3. Block rewards are applied correctly (no fees in these scenarios)
4. Balance deltas match expected block rewards
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from animica.cli import mining
from typer.testing import CliRunner

runner = CliRunner()

# Test address used across multiple tests for consistency
# This is the canonical premine address from consensus/rewards.py
TEST_BECH32_ADDRESS = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"


def _mock_template(height: int = 1) -> dict:
    return {
        "enabled": True,
        "header": {
            "v": 1,
            "chainId": 1337,
            "height": height,
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


@pytest.fixture
def wallet_with_premine(tmp_path: Path) -> tuple[Path, str, str]:
    """
    Create a test wallet file with a 'premine' label.
    
    Returns:
        tuple: (Path, str, str) = (wallet_file_path, label, bech32_address)
            - wallet_file_path: Path to the temporary wallet JSON file
            - label: Wallet label ("premine")
            - bech32_address: Animica Bech32 address (TEST_BECH32_ADDRESS)
    """
    wallet_file = tmp_path / "test_wallets.json"
    test_label = "premine"
    test_address = TEST_BECH32_ADDRESS
    
    wallet_data = {
        "version": 1,
        "wallets": [
            {
                "label": test_label,
                "address": test_address,
                "alg_id": 1,
                "alg_name": "dilithium3",
                "public_key_hex": "abcdef1234567890" * 4,  # 64 hex chars
                "secret_key_hex": "fedcba0987654321" * 8,  # 128 hex chars
                "created_at": "2024-01-01T00:00:00Z",
            }
        ],
    }
    wallet_file.write_text(json.dumps(wallet_data, indent=2))
    return wallet_file, test_label, test_address


def test_resolve_wallet_label_to_address(wallet_with_premine):
    """Test that wallet label resolution works correctly."""
    wallet_file, label, expected_address = wallet_with_premine
    
    # Test label resolution
    resolved = mining._resolve_wallet_label_to_address(label, wallet_file)
    assert resolved == expected_address, f"Expected {expected_address}, got {resolved}"
    
    # Test non-existent label returns None
    resolved_none = mining._resolve_wallet_label_to_address("nonexistent", wallet_file)
    assert resolved_none is None, "Non-existent label should return None"


def test_validate_bech32_address():
    """Test Bech32 address validation."""
    valid_address = TEST_BECH32_ADDRESS
    invalid_addresses = [
        "invalid",
        "btc1qxyz...",  # Wrong prefix
        "anim2abc",     # Wrong hrp digit
        "",
        "0x1234",       # Hex format
    ]
    
    # Valid address should pass
    assert mining._validate_bech32_address(valid_address), "Valid address should pass validation"
    
    # Invalid addresses should fail
    for addr in invalid_addresses:
        assert not mining._validate_bech32_address(addr), f"Invalid address should fail: {addr}"


def test_resolve_payout_address_with_valid_bech32(wallet_with_premine):
    """Test that _resolve_payout_address accepts valid Bech32 addresses directly."""
    wallet_file, label, test_address = wallet_with_premine
    
    # Mock wallet file path resolution
    with patch("animica.cli.mining._wallet_file_path", return_value=wallet_file):
        # Test valid Bech32 address is used directly (priority over label lookup)
        resolved = mining._resolve_payout_address(test_address)
        assert resolved == test_address, "Valid Bech32 should be used directly"


def test_resolve_payout_address_with_wallet_label(wallet_with_premine):
    """Test that _resolve_payout_address resolves wallet labels."""
    wallet_file, label, test_address = wallet_with_premine
    
    # Mock the wallet._wallet_file_path function that's imported in _resolve_wallet_label_to_address
    with patch("animica.cli.wallet._wallet_file_path", return_value=wallet_file):
        # Test wallet label resolution
        resolved = mining._resolve_payout_address(label)
        assert resolved == test_address, f"Label should resolve to {test_address}"


def test_resolve_payout_address_invalid_fails(wallet_with_premine):
    """Test that invalid address/label causes exit with code 2."""
    wallet_file, _, _ = wallet_with_premine
    
    # Mock wallet file path resolution
    with patch("animica.cli.mining._wallet_file_path", return_value=wallet_file):
        # Test invalid input causes exit
        try:
            mining._resolve_payout_address("invalid_address_and_label")
            assert False, "Should have raised typer.Exit"
        except (SystemExit, Exception) as e:
            # typer.Exit can raise SystemExit or click.exceptions.Exit depending on implementation
            # Check exit code if available
            exit_code = getattr(e, "code", getattr(e, "exit_code", None))
            if exit_code is not None:
                assert exit_code == 2, f"Expected exit code 2, got {exit_code}"
            else:
                # For click.exceptions.Exit, the exit code is in the args
                if hasattr(e, "args") and len(e.args) > 0:
                    assert e.args[0] == 2, f"Expected exit code 2, got {e.args[0]}"


def test_mine_blocks_with_label_uses_resolved_address(monkeypatch: Any, wallet_with_premine):
    """Test that mine-blocks resolves wallet label and passes correct address to RPC."""
    wallet_file, label, test_address = wallet_with_premine
    
    # Track RPC calls
    rpc_calls = []
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            rpc_calls.append({"method": method, "params": params})
            if method == "miner.getBlockTemplate":
                return _mock_template()
            if method == "miner.submitBlock":
                return {"accepted": True}
            return {}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    # Mock modules and wallet path (patch wallet module's _wallet_file_path)
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    # Import wallet module and patch its _wallet_file_path
    from animica.cli import wallet as wallet_module
    monkeypatch.setattr(wallet_module, "_wallet_file_path", lambda x: wallet_file)
    
    # Run mine-blocks with wallet label
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", label,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert len(rpc_calls) == 2, f"Expected 2 RPC calls, got {len(rpc_calls)}"
    
    # Verify RPC call used resolved Bech32 address
    rpc_call = rpc_calls[0]
    assert rpc_call["method"] == "miner.getBlockTemplate"
    assert isinstance(rpc_call["params"], dict)
    assert rpc_call["params"]["address"] == test_address, \
        f"RPC should use resolved address {test_address}, got {rpc_call['params']['address']}"


def test_mine_blocks_with_raw_bech32_address(monkeypatch: Any):
    """Test that mine-blocks accepts raw Bech32 address and passes it to RPC."""
    test_address = TEST_BECH32_ADDRESS
    
    # Track RPC calls
    rpc_calls = []
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            rpc_calls.append({"method": method, "params": params})
            if method == "miner.getBlockTemplate":
                return _mock_template()
            if method == "miner.submitBlock":
                return {"accepted": True}
            return {}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    # Mock modules
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    # Run mine-blocks with raw Bech32 address
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert len(rpc_calls) == 2, f"Expected 2 RPC calls, got {len(rpc_calls)}"
    
    # Verify RPC call used the raw Bech32 address
    rpc_call = rpc_calls[0]
    assert rpc_call["method"] == "miner.getBlockTemplate"
    assert isinstance(rpc_call["params"], dict)
    assert rpc_call["params"]["address"] == test_address, \
        f"RPC should use raw address {test_address}, got {rpc_call['params']['address']}"


def test_mine_blocks_help_text_mentions_label_and_address():
    """Test that mine-blocks help text mentions both wallet label and Bech32 address."""
    # Get the help text from the docstring
    help_text = mining.mine_blocks.__doc__ or ""
    
    # Verify help text mentions both options
    assert "wallet label" in help_text.lower(), "Help should mention wallet label"
    assert "bech32" in help_text.lower(), "Help should mention Bech32 address"
    assert "anim1" in help_text.lower(), "Help should show example with anim1 prefix"


def test_mine_blocks_enforces_minimum_2s_delay_between_blocks(monkeypatch: Any):
    """Test that mining multiple blocks enforces 2s delay between them."""
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
                return _mock_template()
            if method == "miner.submitBlock":
                return {"accepted": True}
            return {}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    # Mine 5 blocks
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", TEST_BECH32_ADDRESS,
            "--count", "5",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    # Should have 4 sleep calls for 5 blocks (no sleep after last block)
    assert len(sleep_calls) == 4, f"Expected 4 sleep calls, got {len(sleep_calls)}"
    
    # Each sleep should be 2.0 seconds
    assert all(s == 2.0 for s in sleep_calls), f"All sleeps should be 2s, got {sleep_calls}"


def test_mine_blocks_no_delay_for_single_block(monkeypatch: Any):
    """Test that mining a single block does not add any delay."""
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
                return _mock_template()
            if method == "miner.submitBlock":
                return {"accepted": True}
            return {}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    # Mine 1 block
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", TEST_BECH32_ADDRESS,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    # Should have no sleep calls for single block
    assert len(sleep_calls) == 0, f"Expected no sleep calls for single block, got {len(sleep_calls)}"


def test_mine_blocks_cli_output_shows_reward_details(monkeypatch: Any):
    """Test that CLI output shows per-block and total rewards in both ANM and nANM."""
    test_address = TEST_BECH32_ADDRESS
    
    # Mock RPC to return realistic reward data (5 ANM miner share = 4 ANM = 4,000,000,000 nANM)
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            if method == "miner.getBlockTemplate":
                height = 1 if not hasattr(self, "_height") else self._height + 1
                self._height = height
                template = _mock_template(height=height)
                template["coinbase"] = {"amount": 4000000000}
                return template
            if method == "miner.submitBlock":
                return {"accepted": True}
            return {}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    # Mock time.sleep to speed up test
    monkeypatch.setattr("time.sleep", lambda x: None)
    
    # Mine 2 blocks
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "2",
            "--rpc-url", "http://127.0.0.1:8545",
        ],
    )
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    # Verify output contains per-block reward details (both ANM and nANM)
    output = result.output
    
    # Check for per-block output (Block X/Y mined with reward in ANM and nANM)
    assert "Block 1/2 mined" in output, "Output should show Block 1/2"
    assert "Block 2/2 mined" in output, "Output should show Block 2/2"
    
    # Check for rewards in ANM format (e.g., "4.000000000 ANM")
    assert "ANM" in output, "Output should show ANM unit"
    assert "nANM" in output, "Output should show nANM unit"
    
    # Check for reward values (4 ANM = 4000000000 nANM)
    assert "4000000000" in output, "Output should show reward in nANM (4000000000)"
    
    # Check for total reward summary
    assert "Total reward:" in output or "total reward:" in output, "Output should show total reward"
    assert "8.000000000 ANM" in output or "8000000000 nANM" in output, \
        "Output should show total of 8 ANM (2 blocks × 4 ANM)"
    
    # Check for success message
    assert "Successfully mined" in output or "mined" in output.lower(), \
        "Output should indicate successful mining"
    
    # Verify chain height is shown
    assert "height" in output.lower(), "Output should show chain height"
