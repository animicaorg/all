"""Tests for faucet network resolution and propagation."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Generator
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from animica.cli import faucet
from animica.cli.state import CLIState

runner = CliRunner()


@pytest.fixture
def clean_env(monkeypatch: Any) -> Generator[None, None, None]:
    """Clean environment fixture."""
    saved_rpc = os.environ.get("ANIMICA_RPC_URL")
    saved_network = os.environ.get("ANIMICA_NETWORK")
    
    yield
    
    if saved_rpc is not None:
        os.environ["ANIMICA_RPC_URL"] = saved_rpc
    else:
        os.environ.pop("ANIMICA_RPC_URL", None)
    
    if saved_network is not None:
        os.environ["ANIMICA_NETWORK"] = saved_network
    else:
        os.environ.pop("ANIMICA_NETWORK", None)


def _mock_successful_response() -> Mock:
    """Create a mock successful RPC response."""
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "address": "anim1test",
            "amount": "0x1bc16d674ec80000",
            "balance": "0x1bc16d674ec80000",
            "message": "Faucet credited"
        },
    }
    return response


def _mock_mainnet_error_response() -> Mock:
    """Create a mock mainnet error RPC response."""
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32000,
            "message": "Faucet is not available on mainnet",
            "data": {
                "chainId": 1,
                "reason": "mainnet_disabled"
            }
        },
    }
    return response


def test_faucet_uses_persisted_network_setting(monkeypatch: Any, clean_env: Any) -> None:
    """Test that faucet respects persisted network setting from 'animica network set'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "testnet")
        
        # Patch both modules to use the temporary state
        monkeypatch.setattr("animica.cli.faucet.get_cli_state", lambda: CLIState(state_file))
        
        # Clear environment variables to test state-based resolution
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        # Mock the RPC call
        with patch('animica.cli.faucet.requests.post') as mock_post:
            mock_post.return_value = _mock_successful_response()
            
            result = runner.invoke(
                faucet.app,
                ["request", "anim1test"]
            )
            
            # Should succeed with testnet RPC URL
            assert result.exit_code == 0, f"Output: {result.output}"
            assert "Using network: testnet" in result.output
            assert "Faucet request successful" in result.output
            
            # Verify the correct RPC URL was called (testnet: port 18546)
            assert mock_post.called
            call_args = mock_post.call_args
            assert "http://127.0.0.1:18546/rpc" in str(call_args)


def test_faucet_env_var_overrides_persisted_network(monkeypatch: Any, clean_env: Any) -> None:
    """Test that ANIMICA_NETWORK env var overrides persisted network setting."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "mainnet")
        
        monkeypatch.setattr("animica.cli.faucet.get_cli_state", lambda: CLIState(state_file))
        
        # Set environment to devnet (should override state)
        monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        
        # Mock the RPC call
        with patch('animica.cli.faucet.requests.post') as mock_post:
            mock_post.return_value = _mock_successful_response()
            
            result = runner.invoke(
                faucet.app,
                ["request", "anim1test"]
            )
            
            # Should use devnet, not mainnet
            assert result.exit_code == 0, f"Output: {result.output}"
            assert "Using network: devnet" in result.output
            
            # Verify the correct RPC URL was called (devnet: port 28545)
            assert mock_post.called
            call_args = mock_post.call_args
            assert "http://127.0.0.1:28545/rpc" in str(call_args)


def test_faucet_network_flag_overrides_all(monkeypatch: Any, clean_env: Any) -> None:
    """Test that --network flag has highest priority."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "mainnet")
        
        monkeypatch.setattr("animica.cli.faucet.get_cli_state", lambda: CLIState(state_file))
        
        # Set env to devnet
        monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        
        # Mock the RPC call
        with patch('animica.cli.faucet.requests.post') as mock_post:
            mock_post.return_value = _mock_successful_response()
            
            result = runner.invoke(
                faucet.app,
                ["request", "anim1test", "--network", "testnet"]
            )
            
            # Should use testnet (from --network flag)
            assert result.exit_code == 0, f"Output: {result.output}"
            assert "Using network: testnet" in result.output
            
            # Verify the correct RPC URL was called (testnet: port 18546)
            assert mock_post.called
            call_args = mock_post.call_args
            assert "http://127.0.0.1:18546/rpc" in str(call_args)


def test_faucet_defaults_to_mainnet_when_nothing_set(monkeypatch: Any, clean_env: Any) -> None:
    """Test that faucet defaults to mainnet when no network is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        
        monkeypatch.setattr("animica.cli.faucet.get_cli_state", lambda: CLIState(state_file))
        
        # Clear all network configuration
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        # Mock the RPC call for mainnet error
        with patch('animica.cli.faucet.requests.post') as mock_post:
            mock_post.return_value = _mock_mainnet_error_response()
            
            result = runner.invoke(
                faucet.app,
                ["request", "anim1test"]
            )
            
            # Should use mainnet (default) and fail with expected error
            assert result.exit_code == 1, f"Output: {result.output}"
            assert "Using network: mainnet" in result.output
            assert "Faucet is not available on mainnet" in result.output
            
            # Verify the correct RPC URL was called (mainnet: port 8545)
            assert mock_post.called
            call_args = mock_post.call_args
            assert "http://127.0.0.1:8545/rpc" in str(call_args)


def test_faucet_rpc_url_override(monkeypatch: Any, clean_env: Any) -> None:
    """Test that --rpc-url flag overrides network-based URL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "mainnet")
        
        monkeypatch.setattr("animica.cli.faucet.get_cli_state", lambda: CLIState(state_file))
        
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        # Mock a custom RPC URL
        custom_url = "http://custom-rpc:9999/rpc"
        with patch('animica.cli.faucet.requests.post') as mock_post:
            mock_post.return_value = _mock_successful_response()
            
            result = runner.invoke(
                faucet.app,
                ["request", "anim1test", "--rpc-url", custom_url]
            )
            
            # Should succeed with custom RPC URL
            assert result.exit_code == 0, f"Output: {result.output}"
            assert "Faucet request successful" in result.output
            
            # Verify the custom RPC URL was called
            assert mock_post.called
            call_args = mock_post.call_args
            assert custom_url in str(call_args)


def test_faucet_verbose_shows_network_resolution(monkeypatch: Any, clean_env: Any) -> None:
    """Test that --verbose flag shows network resolution details."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        
        monkeypatch.setattr("animica.cli.faucet.get_cli_state", lambda: CLIState(state_file))
        
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        # Mock the RPC call
        with patch('animica.cli.faucet.requests.post') as mock_post:
            mock_post.return_value = _mock_successful_response()
            
            result = runner.invoke(
                faucet.app,
                ["request", "anim1test", "--verbose"]
            )
            
            # Should show network resolution details
            assert result.exit_code == 0, f"Output: {result.output}"
            assert "Using network: devnet" in result.output
            # With verbose, should see debug logs
            assert "devnet" in result.output.lower()


def test_faucet_network_resolution_priority(monkeypatch: Any) -> None:
    """Test the internal _resolve_network function priority."""
    from animica.cli.faucet import _resolve_network
    
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "testnet")
        
        monkeypatch.setattr("animica.cli.faucet.get_cli_state", lambda: CLIState(state_file))
        
        # Test 1: ANIMICA_NETWORK env var has highest priority
        monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
        assert _resolve_network() == "devnet"
        
        # Test 2: Persisted state is used when env var is not set
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        assert _resolve_network() == "testnet"
        
        # Test 3: Default is used when nothing is set
        state.delete("active_network")
        assert _resolve_network() == "mainnet"


def test_faucet_rpc_url_resolution_priority(monkeypatch: Any) -> None:
    """Test the internal _resolve_rpc_url function priority."""
    from animica.cli.faucet import _resolve_rpc_url
    
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "testnet")
        
        monkeypatch.setattr("animica.cli.faucet.get_cli_state", lambda: CLIState(state_file))
        
        # Test 1: CLI arg has highest priority
        monkeypatch.setenv("ANIMICA_RPC_URL", "http://env:8888/rpc")
        monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
        assert _resolve_rpc_url("http://cli:9999/rpc") == "http://cli:9999/rpc"
        
        # Test 2: Env var is used when CLI arg is not provided
        assert _resolve_rpc_url(None) == "http://env:8888/rpc"
        
        # Test 3: Network config is used when neither CLI arg nor env var is set
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        # With ANIMICA_NETWORK set to devnet, should use devnet URL
        assert _resolve_rpc_url(None) == "http://127.0.0.1:28545/rpc"
        
        # Test 4: Without ANIMICA_NETWORK env, should use state (testnet)
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        assert _resolve_rpc_url(None) == "http://127.0.0.1:18546/rpc"
        
        # Test 5: Explicit network parameter overrides everything (except rpc_url and ANIMICA_RPC_URL)
        monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")  # This should be ignored when network param is provided
        assert _resolve_rpc_url(None, "devnet") == "http://127.0.0.1:28545/rpc"
