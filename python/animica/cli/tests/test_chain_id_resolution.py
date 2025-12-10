"""
Tests for chain ID resolution in animica tx CLI commands.

This module specifically tests edge cases and failure scenarios for chain ID
resolution to prevent the 'got: 0, expected: 1' error.
"""
import json
from pathlib import Path
from typing import Optional

import pytest
import respx
import typer
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
                "label": "test_wallet",
                "address": "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f",
                "alg_id": 4098,
                "alg_name": "sphincs_shake_128s",
                "public_key_hex": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
                "secret_key_hex": "0011223344556677889900112233445566778899001122334455667788990011",
                "created_at": "2025-01-01T00:00:00Z"
            }
        ]
    }
    wallet_file.write_text(json.dumps(store, indent=2))
    return wallet_file


# ============================================================================
# Chain ID Resolution Edge Cases
# ============================================================================

@respx.mock
def test_build_auto_detects_chain_id(wallet_store: Path) -> None:
    """Test that build command auto-detects chain ID from node."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock node returning chain ID 1337
    respx.post(rpc_url).mock(side_effect=[
        # Response for chain.getChainId
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 1337}),
        # Response for state.getTransactionCount
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": 0}),
    ])
    
    result = runner.invoke(tx.app, [
        "build",
        "--from", "anim1test",
        "--to", "anim1dest",
        "--value", "1.0",
        "--rpc-url", rpc_url
    ])
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert "chainId" in result.output or "Transaction (unsigned)" in result.output


@respx.mock
def test_build_explicit_chain_id_matches_node(wallet_store: Path) -> None:
    """Test that build validates explicit chain ID against node."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock node returning chain ID 42
    respx.post(rpc_url).mock(side_effect=[
        # Response for chain.getChainId - node says 42
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 42}),
        # Response for state.getTransactionCount
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": 0}),
    ])
    
    result = runner.invoke(tx.app, [
        "build",
        "--from", "anim1test",
        "--to", "anim1dest",
        "--value", "1.0",
        "--chain-id", "42",
        "--rpc-url", rpc_url
    ])
    
    assert result.exit_code == 0, f"Command failed: {result.output}"


@respx.mock
def test_build_explicit_chain_id_mismatch_fails(wallet_store: Path) -> None:
    """Test that build fails clearly when explicit chain ID doesn't match node."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock node returning chain ID 1
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": 1})
    
    result = runner.invoke(tx.app, [
        "build",
        "--from", "anim1test",
        "--to", "anim1dest",
        "--value", "1.0",
        "--chain-id", "99",  # Mismatch
        "--rpc-url", rpc_url
    ])
    
    assert result.exit_code != 0
    assert "Chain ID mismatch" in result.output
    assert "CLI chain ID:  99" in result.output
    assert "Node chain ID: 1" in result.output


@respx.mock
def test_build_node_unreachable_fails_clearly(wallet_store: Path) -> None:
    """Test that build fails with clear error when node is unreachable."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock connection error
    respx.post(rpc_url).mock(side_effect=httpx.ConnectError("Connection refused"))
    
    result = runner.invoke(tx.app, [
        "build",
        "--from", "anim1test",
        "--to", "anim1dest",
        "--value", "1.0",
        "--rpc-url", rpc_url
    ])
    
    assert result.exit_code != 0
    assert "Could not query node's chain ID" in result.output or "error" in result.output.lower()


@respx.mock
def test_build_node_returns_null_chain_id(wallet_store: Path) -> None:
    """Test that build fails clearly when node returns null/invalid chain ID."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock node returning null chain ID
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": None})
    
    result = runner.invoke(tx.app, [
        "build",
        "--from", "anim1test",
        "--to", "anim1dest",
        "--value", "1.0",
        "--rpc-url", rpc_url
    ])
    
    assert result.exit_code != 0
    assert "invalid chain ID" in result.output.lower() or "null" in result.output.lower()


@respx.mock
def test_build_saves_correct_chain_id_to_file(tmp_path: Path, wallet_store: Path) -> None:
    """Test that build saves the correct chain ID to output file."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    output_file = tmp_path / "tx.json"
    
    # Mock node returning chain ID 1337
    respx.post(rpc_url).mock(side_effect=[
        # Response for chain.getChainId
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 1337}),
        # Response for state.getTransactionCount
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": 5}),
    ])
    
    result = runner.invoke(tx.app, [
        "build",
        "--from", "anim1test",
        "--to", "anim1dest",
        "--value", "1.0",
        "--output", str(output_file),
        "--rpc-url", rpc_url
    ])
    
    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert output_file.exists()
    
    tx_data = json.loads(output_file.read_text())
    assert tx_data["chainId"] == 1337


@respx.mock
def test_build_env_var_chain_id_validated(wallet_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that build validates ANIMICA_CHAIN_ID env var against node."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Set env var to chain ID 77
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "77")
    
    # Mock node returning chain ID 77
    respx.post(rpc_url).mock(side_effect=[
        # Response for chain.getChainId
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 77}),
        # Response for state.getTransactionCount
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": 0}),
    ])
    
    result = runner.invoke(tx.app, [
        "build",
        "--from", "anim1test",
        "--to", "anim1dest",
        "--value", "1.0",
        "--rpc-url", rpc_url
    ])
    
    assert result.exit_code == 0, f"Command failed: {result.output}"


@respx.mock
def test_build_env_var_chain_id_mismatch_fails(wallet_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that build fails when ANIMICA_CHAIN_ID env var doesn't match node."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Set env var to chain ID 88
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "88")
    
    # Mock node returning chain ID 1
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": 1})
    
    result = runner.invoke(tx.app, [
        "build",
        "--from", "anim1test",
        "--to", "anim1dest",
        "--value", "1.0",
        "--rpc-url", rpc_url
    ])
    
    assert result.exit_code != 0
    assert "Chain ID mismatch" in result.output
    assert "CLI chain ID:  88" in result.output
    assert "Node chain ID: 1" in result.output


# ============================================================================
# Sign Command Chain ID Validation
# ============================================================================

@respx.mock
def test_sign_validates_chain_id(tmp_path: Path, wallet_store: Path) -> None:
    """Test that sign command validates chain ID before signing."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Create a transaction file with chain ID 42
    tx_file = tmp_path / "tx.json"
    tx_data = {
        "from": "anim1test",
        "to": "anim1dest",
        "value": 1000000000000000000,
        "data": "0x",
        "gas": 21000,
        "gasPrice": 1000000000,
        "nonce": 0,
        "chainId": 42
    }
    tx_file.write_text(json.dumps(tx_data))
    
    # Mock node returning chain ID 42 (matching)
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": 42})
    
    result = runner.invoke(tx.app, [
        "sign",
        "--file", str(tx_file),
        "--key", "0",
        "--rpc-url", rpc_url
    ])
    
    # Should fail with "not fully implemented" but after validating chain ID
    assert "Chain ID validated: 42" in result.output or "not yet fully implemented" in result.output


@respx.mock
def test_sign_detects_chain_id_mismatch(tmp_path: Path, wallet_store: Path) -> None:
    """Test that sign command detects chain ID mismatch before attempting to sign."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Create a transaction file with chain ID 99
    tx_file = tmp_path / "tx.json"
    tx_data = {
        "from": "anim1test",
        "to": "anim1dest",
        "value": 1000000000000000000,
        "data": "0x",
        "gas": 21000,
        "gasPrice": 1000000000,
        "nonce": 0,
        "chainId": 99
    }
    tx_file.write_text(json.dumps(tx_data))
    
    # Mock node returning chain ID 1 (mismatch)
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": 1})
    
    result = runner.invoke(tx.app, [
        "sign",
        "--file", str(tx_file),
        "--key", "0",
        "--rpc-url", rpc_url
    ])
    
    assert result.exit_code != 0
    assert "Chain ID mismatch" in result.output


@respx.mock
def test_sign_warns_missing_chain_id(tmp_path: Path, wallet_store: Path) -> None:
    """Test that sign command warns when transaction has no chain ID."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Create a transaction file WITHOUT chain ID
    tx_file = tmp_path / "tx.json"
    tx_data = {
        "from": "anim1test",
        "to": "anim1dest",
        "value": 1000000000000000000,
        "data": "0x",
        "gas": 21000,
        "gasPrice": 1000000000,
        "nonce": 0
    }
    tx_file.write_text(json.dumps(tx_data))
    
    result = runner.invoke(tx.app, [
        "sign",
        "--file", str(tx_file),
        "--key", "0",
        "--rpc-url", rpc_url
    ])
    
    # Should warn about missing chain ID
    assert "has no chain ID" in result.output or "may be rejected" in result.output


def test_sign_invalid_chain_id_format(tmp_path: Path, wallet_store: Path) -> None:
    """Test that sign command handles invalid chain ID format gracefully."""
    rpc_url = "http://localhost:9999/rpc"
    
    # Create a transaction file with invalid chain ID (string instead of int)
    tx_file = tmp_path / "tx.json"
    tx_data = {
        "from": "anim1test",
        "to": "anim1dest",
        "value": 1000000000000000000,
        "data": "0x",
        "gas": 21000,
        "gasPrice": 1000000000,
        "nonce": 0,
        "chainId": "invalid"
    }
    tx_file.write_text(json.dumps(tx_data))
    
    result = runner.invoke(tx.app, [
        "sign",
        "--file", str(tx_file),
        "--key", "0",
        "--rpc-url", rpc_url
    ])
    
    # Should fail with clear error about invalid format
    assert result.exit_code != 0
    assert "Invalid chain ID" in result.output or "valid integer" in result.output


# ============================================================================
# PQ Unsafe Mode Detection
# ============================================================================

@respx.mock
def test_send_warns_about_pq_unsafe_mode(wallet_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that send command warns when using ANIMICA_UNSAFE_PQ_FAKE."""
    import httpx
    rpc_url = "http://localhost:9999/rpc"
    
    # Ensure unsafe mode is enabled
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    
    # Mock all necessary RPC calls
    respx.post(rpc_url).mock(side_effect=[
        # Response for chain.getChainId
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 1337}),
        # Response for state.getTransactionCount
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": 0}),
        # Response for state.suggestGasPrice
        httpx.Response(200, json={"jsonrpc": "2.0", "id": 3, "result": "1000000000"}),
    ])
    
    result = runner.invoke(tx.app, [
        "send",
        "--wallet-file", str(wallet_store),
        "--from", "test_wallet",
        "--to", "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km",
        "--value", "1.0",
        "--dry-run",
        "--rpc-url", rpc_url
    ])
    
    # Should warn about unsafe mode
    assert "ANIMICA_UNSAFE_PQ_FAKE" in result.output or "NOT SECURE" in result.output


# ============================================================================
# Resolution Function Unit Tests
# ============================================================================

@respx.mock
def test_resolve_chain_id_none_auto_detects() -> None:
    """Test that resolve_chain_id with None cli_chain_id auto-detects from node."""
    import httpx
    from animica.cli.tx import resolve_chain_id
    
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock node returning chain ID 1337
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": 1337})
    
    result = resolve_chain_id(rpc_url, None)
    assert result == 1337


@respx.mock
def test_resolve_chain_id_matches() -> None:
    """Test that resolve_chain_id succeeds when cli_chain_id matches node."""
    import httpx
    from animica.cli.tx import resolve_chain_id
    
    rpc_url = "http://localhost:9999/rpc"
    
    # Mock node returning chain ID 42
    respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": 42})
    
    result = resolve_chain_id(rpc_url, 42)
    assert result == 42


def test_resolve_chain_id_mismatch_raises() -> None:
    """Test that resolve_chain_id raises typer.Exit on mismatch."""
    import httpx
    import respx
    from animica.cli.tx import resolve_chain_id
    
    rpc_url = "http://localhost:9999/rpc"
    
    with respx.mock:
        # Mock node returning chain ID 1
        respx.post(rpc_url).respond(json={"jsonrpc": "2.0", "id": 1, "result": 1})
        
        with pytest.raises(typer.Exit):
            resolve_chain_id(rpc_url, 99)


def test_resolve_chain_id_node_error_raises() -> None:
    """Test that resolve_chain_id raises typer.Exit when node is unreachable."""
    import httpx
    import respx
    from animica.cli.tx import resolve_chain_id
    
    rpc_url = "http://localhost:9999/rpc"
    
    with respx.mock:
        # Mock connection error
        respx.post(rpc_url).mock(side_effect=httpx.ConnectError("Connection refused"))
        
        with pytest.raises(typer.Exit):
            resolve_chain_id(rpc_url, None)
