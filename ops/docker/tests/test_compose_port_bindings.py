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


def test_mainnet_rpc_port_binding():
    """Test mainnet RPC is bound to 0.0.0.0:8545."""
    compose = load_compose_file("mainnet")
    node_ports = compose["services"]["node"]["ports"]
    
    # Check RPC port binding
    rpc_port_found = False
    for port in node_ports:
        if isinstance(port, str) and ":8545" in port:
            # Should be bound to 0.0.0.0
            assert port.startswith("0.0.0.0:"), f"RPC port not bound to 0.0.0.0: {port}"
            rpc_port_found = True
            break
    
    assert rpc_port_found, "RPC port 8545 not found in mainnet compose"


def test_mainnet_p2p_port_9000():
    """Test mainnet P2P port 9000 is exposed."""
    compose = load_compose_file("mainnet")
    node_ports = compose["services"]["node"]["ports"]
    
    # Check P2P port 9000
    p2p_port_found = False
    for port in node_ports:
        if isinstance(port, str) and ":9000" in port:
            # Should be bound to 0.0.0.0
            assert port.startswith("0.0.0.0:"), f"P2P port 9000 not bound to 0.0.0.0: {port}"
            p2p_port_found = True
            break
    
    assert p2p_port_found, "P2P port 9000 not found in mainnet compose"


def test_testnet_rpc_port_binding():
    """Test testnet RPC is bound to 0.0.0.0:8546."""
    compose = load_compose_file("testnet")
    node_ports = compose["services"]["node"]["ports"]
    
    # Check RPC port binding
    rpc_port_found = False
    for port in node_ports:
        if isinstance(port, str) and ":8546" in port:
            # Should be bound to 0.0.0.0
            assert port.startswith("0.0.0.0:"), f"RPC port not bound to 0.0.0.0: {port}"
            rpc_port_found = True
            break
    
    assert rpc_port_found, "RPC port 8546 not found in testnet compose"


def test_testnet_p2p_port_9000():
    """Test testnet P2P port 9000 is exposed."""
    compose = load_compose_file("testnet")
    node_ports = compose["services"]["node"]["ports"]
    
    # Check P2P port 9000
    p2p_port_found = False
    for port in node_ports:
        if isinstance(port, str) and ":9000" in port:
            # Should be bound to 0.0.0.0
            assert port.startswith("0.0.0.0:"), f"P2P port 9000 not bound to 0.0.0.0: {port}"
            p2p_port_found = True
            break
    
    assert p2p_port_found, "P2P port 9000 not found in testnet compose"


def test_devnet_rpc_port_binding():
    """Test devnet RPC is bound to 0.0.0.0:8545."""
    compose = load_compose_file("devnet")
    node_ports = compose["services"]["node"]["ports"]
    
    # Check RPC port binding
    rpc_port_found = False
    for port in node_ports:
        if isinstance(port, str) and ":8545" in port:
            # Should be bound to 0.0.0.0
            assert port.startswith("0.0.0.0:"), f"RPC port not bound to 0.0.0.0: {port}"
            rpc_port_found = True
            break
    
    assert rpc_port_found, "RPC port 8545 not found in devnet compose"


def test_devnet_p2p_port_9000():
    """Test devnet P2P port 9000 is exposed."""
    compose = load_compose_file("devnet")
    node_ports = compose["services"]["node"]["ports"]
    
    # Check P2P port 9000
    p2p_port_found = False
    for port in node_ports:
        if isinstance(port, str) and ":9000" in port:
            # Should be bound to 0.0.0.0
            assert port.startswith("0.0.0.0:"), f"P2P port 9000 not bound to 0.0.0.0: {port}"
            p2p_port_found = True
            break
    
    assert p2p_port_found, "P2P port 9000 not found in devnet compose"


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
