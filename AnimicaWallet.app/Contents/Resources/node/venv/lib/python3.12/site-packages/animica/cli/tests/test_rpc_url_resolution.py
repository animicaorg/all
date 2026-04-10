"""Tests for RPC URL resolution across CLI modules."""

from __future__ import annotations

import os
from typing import Any, Generator

import pytest
import respx
import httpx
from typer.testing import CliRunner

from animica.cli import node, rpc, wallet

runner = CliRunner()


@pytest.fixture
def clean_env() -> Generator[None, None, None]:
    """Clean environment fixture."""
    saved = os.environ.get("ANIMICA_RPC_URL")
    saved_network = os.environ.get("ANIMICA_NETWORK")
    
    yield
    
    if saved is not None:
        os.environ["ANIMICA_RPC_URL"] = saved
    else:
        os.environ.pop("ANIMICA_RPC_URL", None)
    
    if saved_network is not None:
        os.environ["ANIMICA_NETWORK"] = saved_network
    else:
        os.environ.pop("ANIMICA_NETWORK", None)


def test_node_status_empty_rpc_url_env(clean_env: Any, monkeypatch: Any) -> None:
    """Test that node status falls back to default when ANIMICA_RPC_URL is empty."""
    # Set empty RPC URL
    monkeypatch.setenv("ANIMICA_RPC_URL", "")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Mock the RPC call
    with respx.mock:
        respx.post("http://127.0.0.1:8545/rpc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"height": 10, "hash": "0xabc", "chainId": 1},
                },
            )
        )
        
        result = runner.invoke(node.app, ["status"])
        
        # Should succeed with default RPC URL
        assert result.exit_code == 0
        assert "http://127.0.0.1:8545/rpc" in result.output


def test_node_status_whitespace_rpc_url_env(clean_env: Any, monkeypatch: Any) -> None:
    """Test that node status falls back to default when ANIMICA_RPC_URL is whitespace."""
    # Set whitespace RPC URL
    monkeypatch.setenv("ANIMICA_RPC_URL", "   ")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Mock the RPC call
    with respx.mock:
        respx.post("http://127.0.0.1:8545/rpc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"height": 10, "hash": "0xabc", "chainId": 1},
                },
            )
        )
        
        result = runner.invoke(node.app, ["status"])
        
        # Should succeed with default RPC URL
        assert result.exit_code == 0
        assert "http://127.0.0.1:8545/rpc" in result.output


def test_node_status_unset_rpc_url_env(clean_env: Any, monkeypatch: Any) -> None:
    """Test that node status uses default when ANIMICA_RPC_URL is not set."""
    # Unset RPC URL
    monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Mock the RPC call
    with respx.mock:
        respx.post("http://127.0.0.1:8545/rpc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"height": 10, "hash": "0xabc", "chainId": 1},
                },
            )
        )
        
        result = runner.invoke(node.app, ["status"])
        
        # Should succeed with default RPC URL
        assert result.exit_code == 0
        assert "http://127.0.0.1:8545/rpc" in result.output


def test_rpc_call_empty_rpc_url_env(clean_env: Any, monkeypatch: Any) -> None:
    """Test that rpc call falls back to default when ANIMICA_RPC_URL is empty."""
    # Set empty RPC URL
    monkeypatch.setenv("ANIMICA_RPC_URL", "")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Mock the RPC call
    with respx.mock:
        respx.post("http://127.0.0.1:8545/rpc").mock(
            return_value=httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}},
            )
        )
        
        result = runner.invoke(rpc.app, ["call", "test_method"])
        
        # Should succeed with default RPC URL
        assert result.exit_code == 0


def test_rpc_call_whitespace_rpc_url_env(clean_env: Any, monkeypatch: Any) -> None:
    """Test that rpc call falls back to default when ANIMICA_RPC_URL is whitespace."""
    # Set whitespace RPC URL
    monkeypatch.setenv("ANIMICA_RPC_URL", "   ")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Mock the RPC call
    with respx.mock:
        respx.post("http://127.0.0.1:8545/rpc").mock(
            return_value=httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}},
            )
        )
        
        result = runner.invoke(rpc.app, ["call", "test_method"])
        
        # Should succeed with default RPC URL
        assert result.exit_code == 0


def test_wallet_show_empty_rpc_url_env(clean_env: Any, monkeypatch: Any, tmp_path: Any) -> None:
    """Test that wallet show falls back to default when ANIMICA_RPC_URL is empty."""
    # Set empty RPC URL
    monkeypatch.setenv("ANIMICA_RPC_URL", "")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Create a temporary wallet file with a test wallet
    wallet_file = tmp_path / "wallets.json"
    wallet_file.write_text("""{
        "version": 1,
        "wallets": [{
            "label": "test",
            "address": "anim1testaddress",
            "alg_id": 1,
            "alg_name": "dilithium3",
            "public_key_hex": "abcd",
            "secret_key_hex": "1234",
            "created_at": "2024-01-01T00:00:00Z"
        }]
    }""")
    
    # Set the wallet file via environment variable
    monkeypatch.setenv("ANIMICA_WALLETS_FILE", str(wallet_file))
    
    # Mock the RPC call for balance
    with respx.mock:
        respx.post("http://127.0.0.1:8545/rpc").mock(
            return_value=httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": 1000},
            )
        )
        
        result = runner.invoke(
            wallet.app,
            ["show", "test"]
        )
        
        # Should succeed with default RPC URL
        assert result.exit_code == 0
        assert "anim1testaddress" in result.output


def test_node_resolve_rpc_url_with_cli_arg(monkeypatch: Any) -> None:
    """Test that CLI argument takes precedence over env and config."""
    from animica.cli.node import _resolve_rpc_url
    
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://env:8888/rpc")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # CLI arg should win
    result = _resolve_rpc_url("http://arg:9999/rpc")
    assert result == "http://arg:9999/rpc"


def test_node_resolve_rpc_url_with_empty_cli_arg(monkeypatch: Any) -> None:
    """Test that empty CLI argument falls back to env."""
    from animica.cli.node import _resolve_rpc_url
    
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://env:8888/rpc")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Empty CLI arg should fall back to env
    result = _resolve_rpc_url("")
    assert result == "http://env:8888/rpc"


def test_node_resolve_rpc_url_with_whitespace_cli_arg(monkeypatch: Any) -> None:
    """Test that whitespace CLI argument falls back to env."""
    from animica.cli.node import _resolve_rpc_url
    
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://env:8888/rpc")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Whitespace CLI arg should fall back to env
    result = _resolve_rpc_url("   ")
    assert result == "http://env:8888/rpc"


def test_node_resolve_rpc_url_all_empty_uses_default(monkeypatch: Any) -> None:
    """Test that all empty values fall back to config default."""
    from animica.cli.node import _resolve_rpc_url
    
    monkeypatch.setenv("ANIMICA_RPC_URL", "")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Should fall back to mainnet default
    result = _resolve_rpc_url("")
    assert result == "http://127.0.0.1:8545/rpc"


def test_rpc_resolve_rpc_url_with_empty_arg(monkeypatch: Any) -> None:
    """Test rpc module's URL resolution with empty argument."""
    from animica.cli.rpc import _resolve_rpc_url
    
    monkeypatch.setenv("ANIMICA_RPC_URL", "")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Should fall back to config default
    result = _resolve_rpc_url("")
    assert result == "http://127.0.0.1:8545/rpc"


def test_wallet_resolve_rpc_url_with_empty_arg(monkeypatch: Any) -> None:
    """Test wallet module's URL resolution with empty argument."""
    from animica.cli.wallet import _resolve_rpc_url
    
    monkeypatch.setenv("ANIMICA_RPC_URL", "")
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    # Should fall back to config default
    result = _resolve_rpc_url("")
    assert result == "http://127.0.0.1:8545/rpc"


def test_testnet_default_rpc_url(monkeypatch: Any) -> None:
    """Test that testnet network uses correct default RPC URL."""
    from animica.cli.node import _resolve_rpc_url
    
    monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
    monkeypatch.setenv("ANIMICA_NETWORK", "testnet")
    
    # Should use testnet default
    result = _resolve_rpc_url(None)
    assert result == "http://127.0.0.1:8546/rpc"


def test_devnet_default_rpc_url(monkeypatch: Any) -> None:
    """Test that devnet network uses correct default RPC URL."""
    from animica.cli.node import _resolve_rpc_url
    
    monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
    monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
    
    # Should use devnet default
    result = _resolve_rpc_url(None)
    assert result == "http://127.0.0.1:8545/rpc"
