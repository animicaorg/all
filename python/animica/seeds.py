"""Seed node configuration for Animica networks.

This module provides default seed/bootstrap nodes for different
Animica networks to help nodes discover peers and join the network.
"""

from __future__ import annotations

from typing import Dict, List

# Default P2P ports to try when none is specified
DEFAULT_P2P_PORTS = [30333, 30303, 31333, 31334]

# Seed nodes per network
NETWORK_SEEDS: Dict[str, List[str]] = {
    "mainnet": [
        "144.126.133.21:30333",
    ],
    "testnet": [
        # Add testnet seed nodes here when available
    ],
    "devnet": [
        # Devnet typically uses local nodes
    ],
    "local-devnet": [
        # Local devnet uses local nodes
    ],
}


def get_seed_nodes(network: str) -> List[str]:
    """
    Get seed nodes for a specific network.
    
    Args:
        network: Network name (mainnet, testnet, devnet, local-devnet)
        
    Returns:
        List of seed node addresses (host:port format)
    """
    return NETWORK_SEEDS.get(network, [])


def get_default_ports() -> List[int]:
    """
    Get default P2P ports to try when auto-detecting.
    
    Returns:
        List of port numbers in order of priority
    """
    return DEFAULT_P2P_PORTS.copy()


__all__ = [
    "get_seed_nodes",
    "get_default_ports",
    "NETWORK_SEEDS",
    "DEFAULT_P2P_PORTS",
]
