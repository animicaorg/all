"""
Tests for animica tx send CLI command.
"""
import json
from pathlib import Path
from typing import Optional

import pytest
import respx
from typer.testing import CliRunner

from animica.cli import tx

runner = CliRunner()


@pytest.fixture(autouse=True)
def allow_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow PQ fallback for testing."""
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")


@pytest.fixture
def wallet_store(tmp_path: Path) -> Path:
    """Create a wallet store with test wallet entries."""
    wallet_file = tmp_path / "wallets.json"
    store = {
        "version": 1,
        "wallets": [
            {
                "label": "alice",
                "address": "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f",
                "alg_id": 4098,
                "alg_name": "sphincs_shake_128s",
                "public_key_hex": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                "secret_key_hex": "0011223344556677889900112233445566778899001122334455667788990011",
                "created_at": "2025-01-01T00:00:00Z"
            },
            {
                "label": "bob",
                "address": "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
                "alg_id": 4098,
                "alg_name": "sphincs_shake_128s",
                "public_key_hex": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "secret_key_hex": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "created_at": "2025-01-02T00:00:00Z"
            }
        ]
    }
    wallet_file.write_text(json.dumps(store, indent=2))
    return wallet_file


def run_tx_cli(args: list[str], wallet_file: Optional[Path] = None, expect_success: bool = True) -> tuple[int, str]:
    """Run tx CLI and return (exit_code, output)."""
    # Insert --wallet-file after the command name (e.g., "send")
    if wallet_file is not None:
        # Find where to insert --wallet-file (after command name)
        if len(args) > 0:
            cli_args = [args[0], "--wallet-file", str(wallet_file)] + args[1:]
        else:
            cli_args = args
    else:
        cli_args = args
    
    result = runner.invoke(tx.app, cli_args)
    if expect_success:
        assert result.exit_code == 0, f"Command failed: {result.output}\nExit code: {result.exit_code}"
    return result.exit_code, result.output


# ============================================================================
# Address Resolution Tests
# ============================================================================

def test_send_resolve_from_label(wallet_store: Path) -> None:
    """Test resolving sender from wallet label (dry-run)."""
    _, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--dry-run"
    ], wallet_store)
    
    assert "Dry-Run Mode" in output
    assert "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f" in output
    assert "Transaction built and signed (not broadcast)" in output


def test_send_resolve_from_address(wallet_store: Path) -> None:
    """Test resolving sender from full Bech32 address (dry-run)."""
    _, output = run_tx_cli([
        "send",
        "--from", "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "0.5",
        "--dry-run"
    ], wallet_store)
    
    assert "Dry-Run Mode" in output
    assert "Transaction built and signed (not broadcast)" in output


def test_send_invalid_from_label(wallet_store: Path) -> None:
    """Test error when sender label not found."""
    exit_code, output = run_tx_cli([
        "send",
        "--from", "charlie",  # Doesn't exist
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--dry-run"
    ], wallet_store, expect_success=False)
    
    assert exit_code != 0
    assert "not found" in output.lower()


def test_send_invalid_destination_address(wallet_store: Path) -> None:
    """Test error when destination address is invalid."""
    exit_code, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "invalid_address",  # Invalid format
        "--value", "1.0",
        "--dry-run"
    ], wallet_store, expect_success=False)
    
    assert exit_code != 0
    assert "invalid" in output.lower()


# ============================================================================
# Missing/Invalid Arguments Tests
# ============================================================================

def test_send_missing_from(wallet_store: Path) -> None:
    """Test error when --from is missing."""
    exit_code, output = run_tx_cli([
        "send",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--dry-run"
    ], wallet_store, expect_success=False)
    
    assert exit_code != 0


def test_send_missing_to(wallet_store: Path) -> None:
    """Test error when --to is missing."""
    exit_code, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--value", "1.0",
        "--dry-run"
    ], wallet_store, expect_success=False)
    
    assert exit_code != 0


def test_send_missing_value(wallet_store: Path) -> None:
    """Test error when --value is missing."""
    exit_code, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--dry-run"
    ], wallet_store, expect_success=False)
    
    assert exit_code != 0


def test_send_wallet_not_found(tmp_path: Path) -> None:
    """Test error when wallet file doesn't exist."""
    nonexistent = tmp_path / "nonexistent.json"
    exit_code, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--dry-run"
    ], nonexistent, expect_success=False)
    
    assert exit_code != 0
    assert "not found" in output.lower()


# ============================================================================
# Dry-Run Tests
# ============================================================================

def test_send_dry_run_shows_details(wallet_store: Path) -> None:
    """Test dry-run displays transaction details without broadcasting."""
    _, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "2.5",
        "--gas", "30000",
        "--gas-price", "2.0",
        "--nonce", "5",
        "--chain-id", "1337",
        "--dry-run"
    ], wallet_store)
    
    assert "Dry-Run Mode" in output
    assert "From:" in output
    assert "To:" in output
    assert "Value:      2.5 ANM" in output
    assert "Gas Limit:  30000" in output
    assert "Max Fee:    2.0 gwei" in output
    assert "Nonce:      5" in output
    assert "Chain ID:   1337" in output
    assert "Tx Hash:" in output
    assert "Raw Size:" in output
    assert "Transaction built and signed (not broadcast)" in output


def test_send_dry_run_with_defaults(wallet_store: Path) -> None:
    """Test dry-run with default gas/nonce parameters."""
    _, output = run_tx_cli([
        "send",
        "--from", "bob",
        "--to", "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f",
        "--value", "0.1",
        "--dry-run"
    ], wallet_store)
    
    assert "Dry-Run Mode" in output
    assert "Value:      0.1 ANM" in output
    # Should have auto-populated gas and nonce
    assert "Gas Limit:" in output
    assert "Nonce:" in output


# ============================================================================
# Successful Send Tests (with mocked RPC)
# ============================================================================

@respx.mock
def test_send_successful_broadcast(wallet_store: Path) -> None:
    """Test successful transaction broadcast with mocked RPC."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock chain.getChainId
    respx.post(rpc_url).mock(side_effect=[
        # Response for chain.getChainId
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 1337}),
        # Response for state.getTransactionCount
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": 5}),
        # Response for state.suggestGasPrice
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 3, "result": "1000000000"}),
        # Response for tx.sendRawTransaction
        httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": 4,
            "result": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        })
    ])
    
    _, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.5",
        "--rpc-url", rpc_url
    ], wallet_store)
    
    assert "Transaction Submitted" in output
    assert "Tx Hash: 0x" in output
    assert "Transaction broadcast successfully" in output
    assert "1.5 ANM" in output


@respx.mock
def test_send_with_explicit_params(wallet_store: Path) -> None:
    """Test send with explicit gas, nonce, and chain-id."""
    rpc_url = "http://localhost:9999/rpc"
    
    # Only need to mock tx.sendRawTransaction since we're providing all params
    respx.post(rpc_url).respond(json={
        "jsonrpc": "2.0",
        "id": 1,
        "result": "0xabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd"
    })
    
    _, output = run_tx_cli([
        "send",
        "--from", "bob",
        "--to", "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f",
        "--value", "3.0",
        "--gas", "50000",
        "--gas-price", "5.0",
        "--nonce", "10",
        "--chain-id", "42",
        "--rpc-url", rpc_url
    ], wallet_store)
    
    assert "Transaction Submitted" in output
    assert "Tx Hash: 0x" in output
    assert "3.0 ANM" in output


# ============================================================================
# RPC Error Handling Tests
# ============================================================================

@respx.mock
def test_send_rpc_connection_error(wallet_store: Path) -> None:
    """Test handling of RPC connection errors."""
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock connection error
    respx.post(rpc_url).mock(side_effect=Exception("Connection refused"))
    
    exit_code, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--rpc-url", rpc_url
    ], wallet_store, expect_success=False)
    
    assert exit_code != 0
    assert "error" in output.lower()


@respx.mock
def test_send_rpc_error_response(wallet_store: Path) -> None:
    """Test handling of RPC error responses."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock error responses for all RPC calls
    respx.post(rpc_url).mock(side_effect=[
        # Chain ID succeeds
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 1337}),
        # Nonce succeeds
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": 0}),
        # Gas price succeeds
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 3, "result": "1000000000"}),
        # sendRawTransaction fails
        httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": 4,
            "error": {
                "code": -32000,
                "message": "insufficient funds"
            }
        })
    ])
    
    exit_code, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--rpc-url", rpc_url
    ], wallet_store, expect_success=False)
    
    assert exit_code != 0
    assert "error" in output.lower() or "failed" in output.lower()


# ============================================================================
# Integration-style Tests
# ============================================================================

def test_send_round_trip_dry_run(wallet_store: Path) -> None:
    """Test complete dry-run flow with multiple wallets."""
    # Alice sends to Bob
    _, output1 = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "10.0",
        "--dry-run"
    ], wallet_store)
    
    assert "Dry-Run Mode" in output1
    assert "10.0 ANM" in output1
    
    # Bob sends to Alice
    _, output2 = run_tx_cli([
        "send",
        "--from", "bob",
        "--to", "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f",
        "--value", "5.0",
        "--dry-run"
    ], wallet_store)
    
    assert "Dry-Run Mode" in output2
    assert "5.0 ANM" in output2


def test_send_large_value(wallet_store: Path) -> None:
    """Test sending a large value (edge case)."""
    _, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "999999.123456789",
        "--dry-run"
    ], wallet_store)
    
    assert "Dry-Run Mode" in output
    assert "999999.123456789 ANM" in output


def test_send_small_value(wallet_store: Path) -> None:
    """Test sending a very small value (edge case)."""
    _, output = run_tx_cli([
        "send",
        "--from", "alice",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "0.000000001",
        "--dry-run"
    ], wallet_store)
    
    assert "Dry-Run Mode" in output
    assert "0.000000001 ANM" in output
