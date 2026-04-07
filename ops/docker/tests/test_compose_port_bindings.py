"""Tests for Docker Compose port bindings across networks."""

from __future__ import annotations

import yaml
from pathlib import Path


def load_compose_file(network: str) -> dict:
    """Load and parse a Docker Compose file for a given network."""
    repo_root = Path(__file__).resolve().parents[3]
    
    if network == "devnet":
        compose_path = repo_root / "ops" / "docker" / "docker-compose.devnet.yml"
    elif network == "testnet":
        compose_path = repo_root / "ops" / "docker" / "docker-compose.testnet.yml"
    elif network == "mainnet":
        compose_path = repo_root / "ops" / "docker" / "docker-compose.mainnet.yml"
    else:
        raise ValueError(f"Unknown network: {network}")
    
    with open(compose_path) as f:
        return yaml.safe_load(f)


def _find_port_mapping(compose: dict, port_suffix: str) -> str:
    node_ports = compose["services"]["node"]["ports"]
    for port in node_ports:
        if isinstance(port, str) and port.endswith(port_suffix):
            return port
    raise AssertionError(f"Port mapping ending with {port_suffix} not found")


def test_mainnet_rpc_port_binding():
    """Test mainnet RPC stays host-local by default for operator safety."""
    compose = load_compose_file("mainnet")
    port = _find_port_mapping(compose, ":8545")
    assert port.startswith("127.0.0.1:"), f"RPC port should stay loopback-bound: {port}"


def test_mainnet_p2p_port_9000():
    """Test mainnet P2P metrics port is exposed on all interfaces."""
    compose = load_compose_file("mainnet")
    port = _find_port_mapping(compose, ":9000")
    assert port.startswith("0.0.0.0:"), f"Metrics port 9000 should be public: {port}"


def test_testnet_rpc_port_binding():
    """Test testnet RPC stays host-local by default for operator safety."""
    compose = load_compose_file("testnet")
    port = _find_port_mapping(compose, ":8546")
    assert port.startswith("127.0.0.1:"), f"RPC port should stay loopback-bound: {port}"


def test_testnet_p2p_port_9000():
    """Test testnet metrics port is exposed on all interfaces."""
    compose = load_compose_file("testnet")
    port = _find_port_mapping(compose, ":9000")
    assert port.startswith("0.0.0.0:"), f"Metrics port 9000 should be public: {port}"


def test_devnet_rpc_port_binding():
    """Test devnet RPC stays host-local by default for operator safety."""
    compose = load_compose_file("devnet")
    port = _find_port_mapping(compose, ":8545")
    assert port.startswith("127.0.0.1:"), f"RPC port should stay loopback-bound: {port}"


def test_devnet_p2p_port_9000():
    """Test devnet metrics port is exposed on all interfaces."""
    compose = load_compose_file("devnet")
    port = _find_port_mapping(compose, ":9000")
    assert port.startswith("0.0.0.0:"), f"Metrics port 9000 should be public: {port}"


def test_mainnet_rpc_command():
    """Test mainnet uses correct RPC command."""
    compose = load_compose_file("mainnet")
    node_command = compose["services"]["node"]["command"]
    
    # Should use 'python -m rpc' not 'python -m rpc.main'
    command_str = " ".join(node_command) if isinstance(node_command, list) else node_command
    assert "python -m rpc" in command_str, f"RPC command incorrect: {command_str}"
    assert "python -m rpc.main" not in command_str, f"Old command format still present: {command_str}"


def test_testnet_rpc_command():
    """Test testnet uses correct RPC command."""
    compose = load_compose_file("testnet")
    node_command = compose["services"]["node"]["command"]
    
    # Should use 'python -m rpc' not 'python -m rpc.main'
    command_str = " ".join(node_command) if isinstance(node_command, list) else node_command
    assert "python -m rpc" in command_str, f"RPC command incorrect: {command_str}"
    assert "python -m rpc.main" not in command_str, f"Old command format still present: {command_str}"
