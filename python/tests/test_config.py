"""Tests for network configuration module."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Generator

import pytest
from animica.config import get_network_defaults, load_network_config


@pytest.fixture
def clean_env_vars() -> Generator[None, None, None]:
    """Fixture to save and restore environment variables for clean test isolation."""
    saved_vars = {
        "ANIMICA_NETWORK": os.environ.get("ANIMICA_NETWORK"),
        "ANIMICA_RPC_URL": os.environ.get("ANIMICA_RPC_URL"),
        "ANIMICA_CHAIN_ID": os.environ.get("ANIMICA_CHAIN_ID"),
    }
    
    yield
    
    # Restore original environment
    for key, value in saved_vars.items():
        if value is not None:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def test_get_network_defaults_mainnet() -> None:
    """Test mainnet network defaults."""
    defaults = get_network_defaults("mainnet")
    
    assert defaults["chain_id"] == 1
    assert defaults["rpc_port"] == 8545
    assert defaults["rpc_url"] == "http://127.0.0.1:8545/rpc"
    assert defaults["db_name"] == "mainnet.db"
    assert defaults["data_dir"] == "~/.animica/chain-1"
    assert "docker-compose.mainnet.yml" in str(defaults["compose_file"])


def test_get_network_defaults_testnet() -> None:
    """Test testnet network defaults."""
    defaults = get_network_defaults("testnet")
    
    assert defaults["chain_id"] == 2
    assert defaults["rpc_port"] == 8546
    assert defaults["rpc_url"] == "http://127.0.0.1:8546/rpc"
    assert defaults["db_name"] == "testnet.db"
    assert defaults["data_dir"] == "~/.animica/chain-2"
    assert "docker-compose.testnet.yml" in str(defaults["compose_file"])


def test_get_network_defaults_devnet() -> None:
    """Test devnet network defaults."""
    defaults = get_network_defaults("devnet")
    
    assert defaults["chain_id"] == 1337
    assert defaults["rpc_port"] == 8545
    assert defaults["rpc_url"] == "http://127.0.0.1:8545/rpc"
    assert defaults["db_name"] == "devnet.db"
    assert defaults["data_dir"] == "~/.animica/chain-1337"
    assert "docker-compose.yml" in str(defaults["compose_file"])


def test_get_network_defaults_local_devnet() -> None:
    """Test local-devnet network defaults."""
    defaults = get_network_defaults("local-devnet")
    
    assert defaults["chain_id"] == 1337
    assert defaults["rpc_port"] == 8545
    assert defaults["rpc_url"] == "http://127.0.0.1:8545/rpc"
    assert defaults["db_name"] == "devnet.db"
    assert defaults["data_dir"] == "~/.animica/chain-1337"


def test_get_network_defaults_unknown_returns_mainnet() -> None:
    """Test unknown network returns mainnet defaults."""
    defaults = get_network_defaults("unknown-network")
    
    # Should fall back to mainnet
    assert defaults["chain_id"] == 1
    assert defaults["rpc_port"] == 8545


def test_load_network_config_default() -> None:
    """Test loading network config with defaults."""
    # Clear environment
    old_network = os.environ.get("ANIMICA_NETWORK")
    old_rpc = os.environ.get("ANIMICA_RPC_URL")
    
    try:
        if old_network:
            del os.environ["ANIMICA_NETWORK"]
        if old_rpc:
            del os.environ["ANIMICA_RPC_URL"]
        
        config = load_network_config()
        
        assert config.name == "mainnet"
        assert config.chain_id == 1
        assert config.rpc_port == 8545
        assert config.rpc_url == "http://127.0.0.1:8545/rpc"
        assert config.rpc_host == "127.0.0.1"
    finally:
        # Restore environment
        if old_network:
            os.environ["ANIMICA_NETWORK"] = old_network
        if old_rpc:
            os.environ["ANIMICA_RPC_URL"] = old_rpc


def test_load_network_config_from_env() -> None:
    """Test loading network config from environment."""
    old_network = os.environ.get("ANIMICA_NETWORK")
    
    try:
        os.environ["ANIMICA_NETWORK"] = "testnet"
        
        config = load_network_config()
        
        assert config.name == "testnet"
        assert config.chain_id == 2
        assert config.rpc_port == 8546
    finally:
        # Restore original environment
        if old_network:
            os.environ["ANIMICA_NETWORK"] = old_network
        else:
            os.environ.pop("ANIMICA_NETWORK", None)


def test_load_network_config_explicit_network() -> None:
    """Test loading network config with explicit network parameter."""
    config = load_network_config("devnet")
    
    assert config.name == "devnet"
    assert config.chain_id == 1337
    assert config.rpc_port == 8545


def test_load_network_config_env_override() -> None:
    """Test environment variables override defaults."""
    old_network = os.environ.get("ANIMICA_NETWORK")
    old_rpc = os.environ.get("ANIMICA_RPC_URL")
    old_chain_id = os.environ.get("ANIMICA_CHAIN_ID")
    
    try:
        os.environ["ANIMICA_NETWORK"] = "mainnet"
        os.environ["ANIMICA_RPC_URL"] = "http://custom-host:9999/rpc"
        os.environ["ANIMICA_CHAIN_ID"] = "42"
        
        config = load_network_config()
        
        assert config.name == "mainnet"
        assert config.rpc_url == "http://custom-host:9999/rpc"
        assert config.chain_id == 42
        assert config.rpc_host == "custom-host"
    finally:
        # Restore original environment
        if old_network:
            os.environ["ANIMICA_NETWORK"] = old_network
        else:
            os.environ.pop("ANIMICA_NETWORK", None)
        if old_rpc:
            os.environ["ANIMICA_RPC_URL"] = old_rpc
        else:
            os.environ.pop("ANIMICA_RPC_URL", None)
        if old_chain_id:
            os.environ["ANIMICA_CHAIN_ID"] = old_chain_id
        else:
            os.environ.pop("ANIMICA_CHAIN_ID", None)


def test_compose_file_paths_exist() -> None:
    """Test that compose file paths point to expected locations."""
    repo_root = Path(__file__).resolve().parents[2]
    
    mainnet_defaults = get_network_defaults("mainnet")
    testnet_defaults = get_network_defaults("testnet")
    devnet_defaults = get_network_defaults("devnet")
    
    # Check paths are under repo root
    assert str(mainnet_defaults["compose_file"]).startswith(str(repo_root))
    assert str(testnet_defaults["compose_file"]).startswith(str(repo_root))
    assert str(devnet_defaults["compose_file"]).startswith(str(repo_root))
    
    # Check expected filenames
    assert mainnet_defaults["compose_file"].name == "docker-compose.mainnet.yml"
    assert testnet_defaults["compose_file"].name == "docker-compose.testnet.yml"
    assert devnet_defaults["compose_file"].name == "docker-compose.yml"


def test_load_network_config_empty_chain_id(clean_env_vars: Any) -> None:
    """Test that empty ANIMICA_CHAIN_ID is treated as unset."""
    os.environ["ANIMICA_NETWORK"] = "mainnet"
    os.environ["ANIMICA_CHAIN_ID"] = ""
    
    # Should not crash and fall back to mainnet default
    config = load_network_config()
    
    assert config.name == "mainnet"
    assert config.chain_id == 1  # mainnet default


def test_load_network_config_whitespace_chain_id(clean_env_vars: Any) -> None:
    """Test that whitespace-only ANIMICA_CHAIN_ID is treated as unset."""
    os.environ["ANIMICA_NETWORK"] = "testnet"
    os.environ["ANIMICA_CHAIN_ID"] = "   "
    
    # Should not crash and fall back to testnet default
    config = load_network_config()
    
    assert config.name == "testnet"
    assert config.chain_id == 2  # testnet default


def test_load_network_config_invalid_chain_id(clean_env_vars: Any) -> None:
    """Test that invalid ANIMICA_CHAIN_ID falls back to default with warning."""
    os.environ["ANIMICA_NETWORK"] = "devnet"
    os.environ["ANIMICA_CHAIN_ID"] = "not-a-number"
    
    # Should not crash and fall back to devnet default
    config = load_network_config()
    
    assert config.name == "devnet"
    assert config.chain_id == 1337  # devnet default


def test_load_network_config_valid_chain_id_override(clean_env_vars: Any) -> None:
    """Test that valid ANIMICA_CHAIN_ID overrides the default."""
    os.environ["ANIMICA_NETWORK"] = "mainnet"
    os.environ["ANIMICA_CHAIN_ID"] = "999"
    
    config = load_network_config()
    
    assert config.name == "mainnet"
    assert config.chain_id == 999  # overridden value


def test_load_network_config_no_chain_id_env(clean_env_vars: Any) -> None:
    """Test that missing ANIMICA_CHAIN_ID uses network default."""
    os.environ["ANIMICA_NETWORK"] = "mainnet"
    # Ensure ANIMICA_CHAIN_ID is not set
    os.environ.pop("ANIMICA_CHAIN_ID", None)
    
    config = load_network_config()
    
    assert config.name == "mainnet"
    assert config.chain_id == 1  # mainnet default
