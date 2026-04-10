"""Integration tests for network propagation across CLI commands."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from animica.cli.state import CLIState
from animica.config import load_network_config


def test_load_network_config_respects_cli_state(monkeypatch: Any) -> None:
    """Test that load_network_config reads from CLI state when env vars are not set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "testnet")
        
        # Patch _get_cli_state_network to use our temp state
        def mock_get_cli_state_network():
            return state.get("active_network")
        
        monkeypatch.setattr("animica.config._get_cli_state_network", mock_get_cli_state_network)
        
        # Clear environment variables
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        monkeypatch.delenv("ANIMICA_CHAIN_ID", raising=False)
        
        # Load config - should use testnet from state
        config = load_network_config()
        
        assert config.name == "testnet"
        assert config.chain_id == 2
        assert config.rpc_url == "http://127.0.0.1:18546/rpc"
        assert config.rpc_port == 18546


def test_load_network_config_env_var_overrides_state(monkeypatch: Any) -> None:
    """Test that ANIMICA_NETWORK env var takes precedence over CLI state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "mainnet")
        
        def mock_get_cli_state_network():
            return state.get("active_network")
        
        monkeypatch.setattr("animica.config._get_cli_state_network", mock_get_cli_state_network)
        
        # Set environment to devnet (should override state)
        monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        monkeypatch.delenv("ANIMICA_CHAIN_ID", raising=False)
        
        # Load config - should use devnet from env
        config = load_network_config()
        
        assert config.name == "devnet"
        assert config.chain_id == 1337
        assert config.rpc_url == "http://127.0.0.1:28545/rpc"


def test_load_network_config_explicit_param_highest_priority(monkeypatch: Any) -> None:
    """Test that explicit network parameter has highest priority."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "mainnet")
        
        def mock_get_cli_state_network():
            return state.get("active_network")
        
        monkeypatch.setattr("animica.config._get_cli_state_network", mock_get_cli_state_network)
        
        # Set environment to devnet
        monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        
        # Load config with explicit parameter - should use testnet
        config = load_network_config("testnet")
        
        assert config.name == "testnet"
        assert config.chain_id == 2
        assert config.rpc_url == "http://127.0.0.1:18546/rpc"


def test_load_network_config_defaults_to_mainnet(monkeypatch: Any) -> None:
    """Test that config defaults to mainnet when nothing is set."""
    # Mock _get_cli_state_network to return None (no state set)
    monkeypatch.setattr("animica.config._get_cli_state_network", lambda: None)
    
    # Clear all configuration
    monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
    monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
    monkeypatch.delenv("ANIMICA_CHAIN_ID", raising=False)
    
    # Load config - should default to mainnet
    config = load_network_config()
    
    assert config.name == "mainnet"
    assert config.chain_id == 1
    assert config.rpc_url == "http://127.0.0.1:8545/rpc"


def test_load_network_config_rpc_url_override(monkeypatch: Any) -> None:
    """Test that ANIMICA_RPC_URL env var overrides network defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "testnet")
        
        def mock_get_cli_state_network():
            return state.get("active_network")
        
        monkeypatch.setattr("animica.config._get_cli_state_network", mock_get_cli_state_network)
        
        # Set custom RPC URL
        custom_url = "http://custom-rpc:9999/rpc"
        monkeypatch.setenv("ANIMICA_RPC_URL", custom_url)
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        # Load config - should use custom RPC URL but testnet chain ID
        config = load_network_config()
        
        assert config.name == "testnet"
        assert config.chain_id == 2
        assert config.rpc_url == custom_url


def test_load_network_config_chain_id_override(monkeypatch: Any) -> None:
    """Test that ANIMICA_CHAIN_ID env var overrides network defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "testnet")
        
        def mock_get_cli_state_network():
            return state.get("active_network")
        
        monkeypatch.setattr("animica.config._get_cli_state_network", mock_get_cli_state_network)
        
        # Set custom chain ID
        monkeypatch.setenv("ANIMICA_CHAIN_ID", "9999")
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        
        # Load config - should use custom chain ID but testnet RPC URL
        config = load_network_config()
        
        assert config.name == "testnet"
        assert config.chain_id == 9999
        assert config.rpc_url == "http://127.0.0.1:18546/rpc"


def test_network_propagation_priority_complete(monkeypatch: Any) -> None:
    """Test the complete priority chain for network resolution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "testnet")
        
        def mock_get_cli_state_network():
            return state.get("active_network")
        
        monkeypatch.setattr("animica.config._get_cli_state_network", mock_get_cli_state_network)
        
        # Test priority: explicit > env > state > default
        
        # 1. Only state set -> use state
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        config = load_network_config()
        assert config.name == "testnet"
        
        # 2. State + env set -> use env
        monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
        config = load_network_config()
        assert config.name == "devnet"
        
        # 3. State + env + explicit -> use explicit
        config = load_network_config("mainnet")
        assert config.name == "mainnet"
