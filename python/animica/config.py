"""Shared configuration helpers for Animica tools.

This module centralizes lightweight network profile handling so
user-facing tools can respect the same environment variables
without hard-coding devnet defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

DEFAULT_NETWORK = "mainnet"
DEFAULT_RPC_URL = "http://127.0.0.1:8545/rpc"


@dataclass(frozen=True)
class NetworkConfig:
    name: str
    rpc_url: str
    chain_id: int
    compose_file: Path
    genesis_path: str
    data_dir: str
    rpc_port: int

    @property
    def rpc_host(self) -> str:
        parsed = urlparse(self.rpc_url)
        return parsed.hostname or "127.0.0.1"


def get_network_defaults(network: str) -> dict[str, any]:
    """
    Get default configuration values for a specific network.
    
    Returns a dictionary with network-specific defaults including:
    - chain_id: Network chain ID
    - rpc_url: Default RPC endpoint URL
    - rpc_port: Default RPC port
    - compose_file: Path to network-specific Docker Compose file
    - genesis_path: Path to genesis file
    - data_dir: Network-specific data directory
    - db_name: Database file name
    """
    # Get repository root (3 levels up from this file)
    repo_root = Path(__file__).resolve().parents[2]
    
    network_configs = {
        "mainnet": {
            "chain_id": 1,
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "rpc_port": 8545,
            "compose_file": repo_root / "ops" / "docker" / "docker-compose.mainnet.yml",
            "genesis_path": "core/genesis/genesis.mainnet.json",
            "data_dir": "~/.animica/chain-1",
            "db_name": "mainnet.db",
        },
        "testnet": {
            "chain_id": 2,
            "rpc_url": "http://127.0.0.1:8546/rpc",
            "rpc_port": 8546,
            "compose_file": repo_root / "ops" / "docker" / "docker-compose.testnet.yml",
            "genesis_path": "core/genesis/genesis.testnet.json",
            "data_dir": "~/.animica/chain-2",
            "db_name": "testnet.db",
        },
        "devnet": {
            "chain_id": 1337,
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "rpc_port": 8545,
            "compose_file": repo_root / "tests" / "devnet" / "docker-compose.yml",
            "genesis_path": "core/genesis/genesis.json",
            "data_dir": "~/.animica/chain-1337",
            "db_name": "devnet.db",
        },
        "local-devnet": {
            "chain_id": 1337,
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "rpc_port": 8545,
            "compose_file": repo_root / "tests" / "devnet" / "docker-compose.yml",
            "genesis_path": "core/genesis/genesis.json",
            "data_dir": "~/.animica/chain-1337",
            "db_name": "devnet.db",
        },
    }
    
    return network_configs.get(network, network_configs["mainnet"])


def load_network_config(network: Optional[str] = None) -> NetworkConfig:
    """
    Load network configuration from environment or defaults.
    
    Priority:
    1. Explicit network parameter
    2. ANIMICA_NETWORK environment variable
    3. DEFAULT_NETWORK constant
    
    Args:
        network: Optional network name override
        
    Returns:
        NetworkConfig with all network-specific settings
    """
    network_name = network or os.getenv("ANIMICA_NETWORK", DEFAULT_NETWORK)
    defaults = get_network_defaults(network_name)
    
    # Allow environment overrides
    rpc_url = os.getenv("ANIMICA_RPC_URL", defaults["rpc_url"])
    chain_id = int(os.getenv("ANIMICA_CHAIN_ID", str(defaults["chain_id"])))
    
    return NetworkConfig(
        name=network_name,
        rpc_url=rpc_url,
        chain_id=chain_id,
        compose_file=defaults["compose_file"],
        genesis_path=defaults["genesis_path"],
        data_dir=defaults["data_dir"],
        rpc_port=defaults["rpc_port"],
    )


__all__ = [
    "NetworkConfig",
    "load_network_config",
    "get_network_defaults",
    "DEFAULT_NETWORK",
    "DEFAULT_RPC_URL",
]
