"""Shared configuration helpers for Animica tools.

This module centralizes lightweight network profile handling so
user-facing tools can respect the same environment variables
without hard-coding devnet defaults.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

DEFAULT_NETWORK = "mainnet"
# Local RPC endpoint (default port 8545)
DEFAULT_RPC_URL = "http://127.0.0.1:8545/rpc"


def _base_data_dir() -> Path:
    """Resolve the root data directory for all Animica assets.

    Priority:
    1. ANIMICA_DATA_DIR (expanded with ~)
    2. ~/.animica
    """

    override = os.environ.get("ANIMICA_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path("~/.animica").expanduser()


def _network_data_dir(chain_id: int) -> str:
    """Return the canonical data directory for a chain id."""

    return str(_base_data_dir() / f"chain-{chain_id}")

logger = logging.getLogger(__name__)


def _get_cli_state_network() -> Optional[str]:
    """
    Get the active network from CLI state if available.
    
    Returns None if CLI state is not available or no network is set.
    This allows config.py to remain independent of the CLI module.
    """
    try:
        # Import here to avoid circular dependency
        from animica.cli.state import get_cli_state
        
        state = get_cli_state()
        return state.get("active_network")
    except ImportError:
        # CLI module not available (e.g., when used as a library)
        return None
    except Exception as e:
        logger.debug(f"Could not read CLI state: {e}")
        return None


@dataclass(frozen=True)
class NetworkConfig:
    name: str
    rpc_url: str
    bootstrap_url: str
    chain_id: int
    compose_file: Path
    genesis_path: str
    data_dir: str
    rpc_port: int
    db_name: str

    @property
    def rpc_host(self) -> str:
        parsed = urlparse(self.rpc_url)
        return parsed.hostname or "127.0.0.1"


def _safe_int_from_env(env_var: str, default: int) -> int:
    """
    Safely parse an integer from an environment variable.
    
    Treats empty string, whitespace, or invalid values as unset.
    Logs a warning when an invalid value is encountered.
    
    Args:
        env_var: Name of the environment variable
        default: Default value to use if env var is unset or invalid
        
    Returns:
        Parsed integer or default value
    """
    value = os.getenv(env_var)
    
    # Treat None (unset) as unset
    if value is None:
        return default
    
    # Treat empty or whitespace-only as unset
    value = value.strip()
    if not value:
        return default
    
    # Try to parse as integer
    try:
        return int(value)
    except ValueError:
        logger.warning(
            f"Invalid value for {env_var}: '{value}' (expected integer). "
            f"Falling back to default: {default}"
        )
        return default


def _env_bool(env_var: str, default: bool = False) -> bool:
    """
    Parse a boolean environment variable.

    Accepts common truthy values ("1", "true", "yes", "on").
    """
    value = os.getenv(env_var)
    if value is None:
        return default
    value = value.strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def get_network_defaults(network: str) -> dict[str, any]:
    """
    Get default configuration values for a specific network.
    
    Returns a dictionary with network-specific defaults including:
    - chain_id: Network chain ID
    - rpc_url: Default RPC endpoint URL
    - rpc_port: Default RPC port
    - p2p_port: Default P2P port
    - metrics_port: Default metrics port
    - compose_file: Path to network-specific Docker Compose file
    - genesis_path: Path to genesis file
    - data_dir: Network-specific data directory
    - db_name: Database file name
    
    Note: devnet and local-devnet are distinct networks:
    - devnet: Uses ops/docker/docker-compose.devnet.yml (full stack with monitoring)
    - local-devnet: Uses tests/devnet/docker-compose.yml (minimal multi-node setup)
    """
    # Get repository root (3 levels up from this file)
    repo_root = Path(__file__).resolve().parents[2]
    
    network_configs = {
        "mainnet": {
            "chain_id": 1,
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "bootstrap_url": "https://rpc.animica.org/rpc",
            "rpc_port": 8545,
            "p2p_port": 30333,
            "metrics_port": 9000,
            "compose_file": repo_root / "ops" / "docker" / "docker-compose.mainnet.yml",
            "genesis_path": "core/genesis/genesis.json",
            "data_dir": _network_data_dir(1),
            "db_name": "animica.db",
        },
        "testnet": {
            "chain_id": 2,
            "rpc_url": "http://127.0.0.1:18546/rpc",
            "bootstrap_url": "https://rpc.testnet.animica.org/rpc",
            "rpc_port": 18546,
            "p2p_port": 31334,
            "metrics_port": 19000,
            "compose_file": repo_root / "ops" / "docker" / "docker-compose.testnet.yml",
            "genesis_path": "genesis/genesis.sample.testnet.json",
            "data_dir": _network_data_dir(2),
            "db_name": "animica.db",
        },
        "devnet": {
            "chain_id": 1337,
            "rpc_url": "http://127.0.0.1:28545/rpc",
            "bootstrap_url": "http://127.0.0.1:28545/rpc",
            "rpc_port": 28545,
            "p2p_port": 31335,
            "metrics_port": 29000,
            "compose_file": repo_root / "ops" / "docker" / "docker-compose.devnet.yml",
            "genesis_path": "genesis/genesis.sample.devnet.json",
            "data_dir": _network_data_dir(1337),
            "db_name": "animica.db",
        },
        "local-devnet": {
            "chain_id": 1337,
            "rpc_url": "http://127.0.0.1:38545/rpc",
            "bootstrap_url": "http://127.0.0.1:38545/rpc",
            "rpc_port": 38545,
            "p2p_port": 31336,
            "metrics_port": 39000,
            "compose_file": repo_root / "tests" / "devnet" / "docker-compose.yml",
            "genesis_path": "genesis/genesis.sample.devnet.json",
            "data_dir": _network_data_dir(1337),
            "db_name": "animica.db",
        },
    }
    
    return network_configs.get(network, network_configs["mainnet"])


def load_network_config(network: Optional[str] = None) -> NetworkConfig:
    """
    Load network configuration from environment or defaults.
    
    Priority:
    1. Explicit network parameter
    2. ANIMICA_NETWORK environment variable
    3. Persisted setting from 'animica network set' (CLI state)
    4. DEFAULT_NETWORK constant
    
    Environment variable handling:
    - ANIMICA_CHAIN_ID: Must be a valid integer. Empty string or whitespace
      is treated as unset and falls back to network defaults. Invalid values
      log a warning and fall back to defaults.
    - ANIMICA_RPC_URL: Empty string or whitespace is treated as unset and
      falls back to network defaults.
    
    Args:
        network: Optional network name override
        
    Returns:
        NetworkConfig with all network-specific settings
    """
    # Resolve network name with proper priority
    if network:
        # Explicit parameter has highest priority
        network_name = network
    else:
        # Check environment variable
        env_network = os.getenv("ANIMICA_NETWORK")
        if env_network:
            network_name = env_network
        else:
            # Check CLI state (from 'animica network set')
            state_network = _get_cli_state_network()
            if state_network:
                network_name = state_network
                logger.debug(f"Network resolved from CLI state: {network_name}")
            else:
                # Fall back to default
                network_name = DEFAULT_NETWORK
    
    defaults = get_network_defaults(network_name)
    
    # Allow environment overrides
    # Treat empty string as unset for RPC URL
    rpc_url = os.getenv("ANIMICA_RPC_URL")
    if rpc_url is not None:
        rpc_url = rpc_url.strip()
    if not rpc_url:
        rpc_url = defaults["rpc_url"]

    is_bootstrap_node = _env_bool("ANIMICA_BOOTSTRAP_NODE", False)

    bootstrap_url = os.getenv("ANIMICA_BOOTSTRAP_RPC_URL")
    if bootstrap_url is not None:
        bootstrap_url = bootstrap_url.strip()
    if not bootstrap_url:
        if is_bootstrap_node:
            bootstrap_url = ""
        else:
            bootstrap_url = defaults.get("bootstrap_url", defaults["rpc_url"])
    chain_id = _safe_int_from_env("ANIMICA_CHAIN_ID", defaults["chain_id"])

    return NetworkConfig(
        name=network_name,
        rpc_url=rpc_url,
        bootstrap_url=bootstrap_url,
        chain_id=chain_id,
        compose_file=defaults["compose_file"],
        genesis_path=defaults["genesis_path"],
        data_dir=defaults["data_dir"],
        rpc_port=defaults["rpc_port"],
        db_name=defaults["db_name"],
    )


__all__ = [
    "NetworkConfig",
    "load_network_config",
    "get_network_defaults",
    "DEFAULT_NETWORK",
    "DEFAULT_RPC_URL",
]
