"""Tests for Docker Compose port configurations."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def get_repo_root() -> Path:
    """Get repository root path."""
    # From python/animica/cli/tests/test_compose_ports.py, go up 4 levels to /home/runner/work/all/all
    return Path(__file__).resolve().parents[4]


def test_mainnet_compose_exposes_rpc_and_p2p_ports() -> None:
    """Test that mainnet compose file exposes RPC (8545) and P2P (30333, 9000) ports."""
    compose_file = get_repo_root() / "ops" / "docker" / "docker-compose.mainnet.yml"
    
    assert compose_file.exists(), f"Mainnet compose file not found: {compose_file}"
    
    with open(compose_file) as f:
        config = yaml.safe_load(f)
    
    # Check node service ports
    node_service = config["services"]["node"]
    ports = node_service.get("ports", [])
    
    # Convert ports to strings for easier comparison
    port_strs = [str(p) for p in ports]
    
    # Check RPC port 8545 is exposed (may include env var syntax)
    assert any(":8545" in p for p in port_strs), f"RPC port 8545 not exposed. Ports: {port_strs}"
    
    # Check P2P port 30333 is exposed
    assert any(":30333" in p for p in port_strs), f"P2P port 30333 not exposed. Ports: {port_strs}"
    
    # Check P2P port 9000 is exposed
    assert any(":9000" in p for p in port_strs), f"P2P port 9000 not exposed. Ports: {port_strs}"


def test_testnet_compose_exposes_rpc_and_p2p_ports() -> None:
    """Test that testnet compose file exposes RPC (8546) and P2P (30334, 9000) ports."""
    compose_file = get_repo_root() / "ops" / "docker" / "docker-compose.testnet.yml"
    
    assert compose_file.exists(), f"Testnet compose file not found: {compose_file}"
    
    with open(compose_file) as f:
        config = yaml.safe_load(f)
    
    # Check node service ports
    node_service = config["services"]["node"]
    ports = node_service.get("ports", [])
    
    # Convert ports to strings for easier comparison
    port_strs = [str(p) for p in ports]
    
    # Check RPC port 8546 is exposed (may include env var syntax)
    assert any(":8546" in p for p in port_strs), f"RPC port 8546 not exposed. Ports: {port_strs}"
    
    # Check P2P port 30334 is exposed
    assert any(":30334" in p for p in port_strs), f"P2P port 30334 not exposed. Ports: {port_strs}"
    
    # Check P2P port 9000 is exposed
    assert any(":9000" in p for p in port_strs), f"P2P port 9000 not exposed. Ports: {port_strs}"


def test_devnet_compose_exposes_rpc_and_p2p_ports() -> None:
    """Test that devnet compose file exposes RPC (8545) and P2P (30333, 9000) ports."""
    compose_file = get_repo_root() / "ops" / "docker" / "docker-compose.devnet.yml"
    
    assert compose_file.exists(), f"Devnet compose file not found: {compose_file}"
    
    with open(compose_file) as f:
        config = yaml.safe_load(f)
    
    # Check node service ports
    node_service = config["services"]["node"]
    ports = node_service.get("ports", [])
    
    # Convert ports to strings for easier comparison
    port_strs = [str(p) for p in ports]
    
    # Check RPC port 8545 is exposed
    assert any(":8545" in p for p in port_strs), f"RPC port 8545 not exposed. Ports: {port_strs}"
    
    # Check P2P port 30333 is exposed
    assert any(":30333" in p for p in port_strs), f"P2P port 30333 not exposed. Ports: {port_strs}"
    
    # Check P2P port 9000 is exposed
    assert any(":9000" in p for p in port_strs), f"P2P port 9000 not exposed. Ports: {port_strs}"


def test_mainnet_compose_rpc_host_env() -> None:
    """Test that mainnet compose file sets RPC host to 0.0.0.0."""
    compose_file = get_repo_root() / "ops" / "docker" / "docker-compose.mainnet.yml"
    
    with open(compose_file) as f:
        config = yaml.safe_load(f)
    
    node_service = config["services"]["node"]
    env = node_service.get("environment", {})
    
    # Check that ANIMICA_RPC_HOST is set to 0.0.0.0
    rpc_host = env.get("ANIMICA_RPC_HOST", "")
    assert "0.0.0.0" in rpc_host, f"Expected RPC host 0.0.0.0, got: {rpc_host}"


def test_testnet_compose_rpc_host_env() -> None:
    """Test that testnet compose file sets RPC host to 0.0.0.0."""
    compose_file = get_repo_root() / "ops" / "docker" / "docker-compose.testnet.yml"
    
    with open(compose_file) as f:
        config = yaml.safe_load(f)
    
    node_service = config["services"]["node"]
    env = node_service.get("environment", {})
    
    # Check that ANIMICA_RPC_HOST is set to 0.0.0.0
    rpc_host = env.get("ANIMICA_RPC_HOST", "")
    assert "0.0.0.0" in rpc_host, f"Expected RPC host 0.0.0.0, got: {rpc_host}"


def test_devnet_compose_rpc_host_env() -> None:
    """Test that devnet compose file sets RPC host to 0.0.0.0."""
    compose_file = get_repo_root() / "ops" / "docker" / "docker-compose.devnet.yml"
    
    with open(compose_file) as f:
        config = yaml.safe_load(f)
    
    node_service = config["services"]["node"]
    env = node_service.get("environment", {})
    
    # Check that RPC_HOST is set to 0.0.0.0
    rpc_host = env.get("RPC_HOST", "")
    assert "0.0.0.0" in rpc_host, f"Expected RPC host 0.0.0.0, got: {rpc_host}"
