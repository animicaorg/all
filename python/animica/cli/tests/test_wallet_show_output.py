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
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: ("http://127.0.0.1:8545", "test"))
    
    # Mock get_balance to return a test balance
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: 1000000000)

    # Mock head/status RPC calls to avoid network traffic
    def _mock_request_rpc(method, params, rpc_url):
        if method == "chain.getHead":
            return {"height": 1, "hash": "0xabc"}
        if method == "tx.getStatus":
            return {"status": "pending"}
        raise RuntimeError("unexpected method")

    monkeypatch.setattr(wallet, "_request_rpc", _mock_request_rpc)
    
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
        assert isinstance(value, (str, int, float, bool, type(None), list, dict)), \
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
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: ("http://127.0.0.1:8545", "test"))
    
    # Mock get_balance to return a test balance
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: 1000000000)

    # Mock head/status RPC calls to avoid network traffic
    monkeypatch.setattr(wallet, "_request_rpc", lambda method, params, rpc_url: {"height": 1, "hash": "0xabc"} if method == "chain.getHead" else {})
    
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


def test_wallet_show_rpc_failure_exits_nonzero(wallet_with_entry, monkeypatch):
    """Test that wallet show exits non-zero when RPC balance query fails."""
    wallet_file, label = wallet_with_entry

    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: wallet_file)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: ("http://127.0.0.1:8545", "test"))
    monkeypatch.setattr(wallet, "_request_rpc", lambda method, params, rpc_url: {"height": 1, "hash": "0xabc"} if method == "chain.getHead" else {})
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: (_ for _ in ()).throw(RuntimeError("rpc")))

    result = runner.invoke(wallet.app, ["show", label])

    assert result.exit_code != 0
    assert "Failed to fetch balance from chain" in result.output


def test_wallet_show_pending_outgoing_uses_active_statuses_only(wallet_with_entry, monkeypatch):
    """pending_outgoing should include only active, not dropped/not_found/rejected txs."""
    wallet_file, label = wallet_with_entry
    wallet_data = json.loads(wallet_file.read_text())
    wallet_data["wallets"][0]["balance"] = 100
    wallet_data["wallets"][0]["pending_txs"] = [
        {"tx_hash": "0x01", "status": "reserved", "reserve_amount": 11},
        {"tx_hash": "0x02", "status": "mempool_accepted", "reserve_amount": 5},
        {"tx_hash": "0x03", "status": "pending", "reserve_amount": 7},
        {"tx_hash": "0x04", "status": "broadcast", "reserve_amount": 13},
        {"tx_hash": "0x05", "status": "in_block_pending_confirm", "reserve_amount": 3},
        {"tx_hash": "0x06", "status": "dropped", "reserve_amount": 999},
        {"tx_hash": "0x07", "status": "not_found", "reserve_amount": 999},
        {"tx_hash": "0x08", "status": "rejected", "reserve_amount": 999},
        {"tx_hash": "0x09", "status": "expired", "reserve_amount": 999},
    ]
    wallet_file.write_text(json.dumps(wallet_data, indent=2))

    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: wallet_file)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: ("http://127.0.0.1:8545", "test"))
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: 100)
    monkeypatch.setattr(wallet, "_request_rpc", lambda method, params, rpc_url: {"height": 1, "hash": "0xabc"} if method == "chain.getHead" else {})
    result = runner.invoke(wallet.app, ["show", label])
    assert result.exit_code == 0, result.output
    output_data = json.loads(result.output)

    assert output_data["balance"] == 100
    assert output_data["balance_confirmed"] == 100
    assert output_data["pending_outgoing"] == (11 + 5 + 7 + 13 + 3)
    assert output_data["pending_outgoing_count"] == 5
    assert output_data["available_balance"] == (100 - (11 + 5 + 7 + 13 + 3))


def test_wallet_show_pending_outgoing_counts_reserve_amount_once(wallet_with_entry, monkeypatch):
    """pending_outgoing must sum reserve_amount only (not reserve+fee)."""
    wallet_file, label = wallet_with_entry
    wallet_data = json.loads(wallet_file.read_text())
    wallet_data["wallets"][0]["balance"] = 100
    wallet_data["wallets"][0]["pending_txs"] = [
        {
            "tx_hash": "0xabc",
            "status": "mempool_accepted",
            "value": 10,
            "fee_reserved": 1,
            "reserve_amount": 11,
        }
    ]
    wallet_file.write_text(json.dumps(wallet_data, indent=2))

    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: wallet_file)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: ("http://127.0.0.1:8545", "test"))
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: 100)
    monkeypatch.setattr(wallet, "_request_rpc", lambda method, params, rpc_url: {"height": 1, "hash": "0xabc"} if method == "chain.getHead" else {})
    result = runner.invoke(wallet.app, ["show", label])
    assert result.exit_code == 0, result.output
    output_data = json.loads(result.output)

    assert output_data["pending_outgoing"] == 11
    assert output_data["available_balance"] == 89
    # If fee_reserved were double-counted we'd see 12 and 88.
    assert output_data["balance_confirmed"] == 100


def test_wallet_show_marks_confirmed_txs_non_pending(wallet_with_entry, monkeypatch):
    wallet_file, label = wallet_with_entry
    wallet_data = json.loads(wallet_file.read_text())
    wallet_data["wallets"][0]["pending_txs"] = [
        {
            "tx_hash": "0xabc",
            "status": "mempool_accepted",
            "reserve_amount": 11,
        }
    ]
    wallet_file.write_text(json.dumps(wallet_data, indent=2))

    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: wallet_file)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: ("http://127.0.0.1:8545", "test"))
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: 100)

    def _mock_request_rpc(method, params, rpc_url):
        if method == "chain.getHead":
            return {"height": 10, "hash": "0xabc"}
        if method == "tx.getStatus":
            return {
                "status": "confirmed",
                "confirmations": 1,
                "included_height": 10,
            }
        return {}

    monkeypatch.setattr(wallet, "_request_rpc", _mock_request_rpc)

    result = runner.invoke(wallet.app, ["show", label])
    assert result.exit_code == 0, result.output
    output_data = json.loads(result.output)

    assert output_data["pending_txs"][0]["status"] == "confirmed"
    assert output_data["pending_outgoing"] == 0
    assert output_data["pending_outgoing_count"] == 0

    persisted = json.loads(wallet_file.read_text())
    assert persisted["wallets"][0]["pending_txs"][0]["status"] == "confirmed"


def test_wallet_show_normalizes_uppercase_status_payloads(wallet_with_entry, monkeypatch):
    wallet_file, label = wallet_with_entry
    wallet_data = json.loads(wallet_file.read_text())
    wallet_data["wallets"][0]["pending_txs"] = [
        {
            "tx_hash": "0xabc",
            "status": "mempool_accepted",
            "reserve_amount": 11,
        }
    ]
    wallet_file.write_text(json.dumps(wallet_data, indent=2))

    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: wallet_file)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: ("http://127.0.0.1:8545", "test"))
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: 100)

    def _mock_request_rpc(method, params, rpc_url):
        if method == "chain.getHead":
            return {"height": 16, "hash": "0xabc"}
        if method == "tx.getStatus":
            return {
                "status": "CONFIRMED",
                "confirmations": "0x2",
                "includedHeight": "0x10",
            }
        return {}

    monkeypatch.setattr(wallet, "_request_rpc", _mock_request_rpc)

    result = runner.invoke(wallet.app, ["show", label])
    assert result.exit_code == 0, result.output
    output_data = json.loads(result.output)

    assert output_data["pending_txs"][0]["status"] == "confirmed"
    assert output_data["pending_txs"][0]["confirmations"] == 2
    assert output_data["pending_txs"][0]["included_height"] == 16
    assert output_data["pending_outgoing"] == 0


def test_wallet_show_normalizes_rejected_status_payloads(wallet_with_entry, monkeypatch):
    wallet_file, label = wallet_with_entry
    wallet_data = json.loads(wallet_file.read_text())
    wallet_data["wallets"][0]["pending_txs"] = [
        {
            "tx_hash": "0xdef",
            "status": "mempool_accepted",
            "reserve_amount": 19,
        }
    ]
    wallet_file.write_text(json.dumps(wallet_data, indent=2))

    monkeypatch.setattr(wallet, "_wallet_file_path", lambda x: wallet_file)
    monkeypatch.setattr(wallet, "_resolve_rpc_url", lambda x: ("http://127.0.0.1:8545", "test"))
    monkeypatch.setattr(wallet, "get_balance", lambda addr, url: 100)

    def _mock_request_rpc(method, params, rpc_url):
        if method == "chain.getHead":
            return {"height": 16, "hash": "0xabc"}
        if method == "tx.getStatus":
            return {
                "status": "REJECTED",
                "error": "nonce gap",
            }
        return {}

    monkeypatch.setattr(wallet, "_request_rpc", _mock_request_rpc)

    result = runner.invoke(wallet.app, ["show", label])
    assert result.exit_code == 0, result.output
    output_data = json.loads(result.output)

    assert output_data["pending_txs"][0]["status"] == "dropped"
    assert output_data["pending_txs"][0]["drop_reason"] == "nonce gap"
    assert output_data["pending_outgoing"] == 0
