"""
Test that wallet show outputs clean JSON without NUL bytes or binary noise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from animica.cli import wallet
from typer.testing import CliRunner

runner = CliRunner()

# Test address used across tests for consistency
# This is the canonical premine address from consensus/rewards.py
TEST_BECH32_ADDRESS = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"


@pytest.fixture
def wallet_with_entry(tmp_path: Path) -> tuple[Path, str]:
    """
    Create a test wallet file with an entry.
    
    Returns:
        tuple: (Path, str) = (wallet_file_path, label)
            - wallet_file_path: Path to the temporary wallet JSON file
            - label: Wallet label ("test-wallet")
    """
    wallet_file = tmp_path / "test_wallets.json"
    test_label = "test-wallet"
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
    return wallet_file, test_label


def test_wallet_show_outputs_clean_json(wallet_with_entry, monkeypatch):
    """Test that wallet show outputs valid JSON without NUL bytes."""
    wallet_file, label = wallet_with_entry
    
    # Mock _wallet_file_path to use our test file
    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: wallet_file)
    
    # Mock _resolve_rpc_url to avoid network calls
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: "http://127.0.0.1:8545")
    
    # Mock get_balance to return a test balance
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: 1000000000)
    
    # Run wallet show command
    result = runner.invoke(
        wallet.app,
        ["show", label],
    )
    
    # Check exit code
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    # Verify output is valid JSON
    try:
        output_data = json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"Output is not valid JSON: {e}\nOutput: {result.output}")
    
    # Verify no NUL bytes in output
    assert "\x00" not in result.output, "Output contains NUL bytes"
    assert "\0" not in result.output, "Output contains NUL bytes (string form)"
    
    # Verify expected fields are present
    assert "label" in output_data
    assert "address" in output_data
    assert output_data["balance_confirmed"] == 1_000_000_000
    assert output_data["balance_source"] == "chain"
    assert "public_key_hex" in output_data
    # NOTE: secret_key_hex should NOT be present by default (security fix)
    assert "secret_key_hex" not in output_data, "secret_key_hex should not be shown by default"
    
    # Verify all values are valid JSON types (strings, ints, etc.)
    for key, value in output_data.items():
        assert isinstance(value, (str, int, float, bool, type(None))), \
            f"Field {key} has invalid type: {type(value)}"
    
    # Verify hex fields are valid hex strings
    assert all(c in "0123456789abcdef" for c in output_data["public_key_hex"]), \
        "public_key_hex contains non-hex characters"


def test_wallet_show_with_address_arg_outputs_clean_json(wallet_with_entry, monkeypatch):
    """Test that wallet show with address argument outputs clean JSON."""
    wallet_file, label = wallet_with_entry
    
    # Get the address from the wallet
    wallet_data = json.loads(wallet_file.read_text())
    test_address = wallet_data["wallets"][0]["address"]
    
    # Mock _wallet_file_path to use our test file
    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: wallet_file)
    
    # Mock _resolve_rpc_url to avoid network calls
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: "http://127.0.0.1:8545")
    
    # Mock get_balance to return a test balance
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: 1000000000)
    
    # Run wallet show command with address
    result = runner.invoke(
        wallet.app,
        ["show", test_address],
    )
    
    # Check exit code
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    # Verify output is valid JSON
    try:
        output_data = json.loads(result.output)
    except json.JSONDecodeError as e:
        pytest.fail(f"Output is not valid JSON: {e}\nOutput: {result.output}")
    
    # Verify no NUL bytes in output
    assert "\x00" not in result.output, "Output contains NUL bytes"
    
    # Verify address matches
    assert output_data["address"] == test_address
    assert output_data["balance_source"] == "chain"


def test_wallet_show_balance_none_is_json_null(wallet_with_entry, monkeypatch):
    """Test that wallet show outputs null for balance when RPC fails."""
    wallet_file, label = wallet_with_entry
    
    # Mock _wallet_file_path to use our test file
    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: wallet_file)
    
    # Mock _resolve_rpc_url to avoid network calls
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: "http://127.0.0.1:8545")
    
    # Mock get_balance to raise an error (RPC failure)
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: (_ for _ in ()).throw(RuntimeError("rpc")))
    
    # Run wallet show command
    result = runner.invoke(
        wallet.app,
        ["show", label],
    )
    
    # Check exit code
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    # Verify output is valid JSON
    output_data = json.loads(result.output)
    
    # Verify balance is JSON null (None in Python)
    assert output_data["balance_confirmed"] is None, "Balance should be null when RPC fails"
    assert output_data["balance_source"] == "cached"
