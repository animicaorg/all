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


def _is_docker_env() -> bool:
    if os.environ.get("ANIMICA_DOCKER") == "1":
        return True
    if Path("/.dockerenv").exists():
        return True
    if Path("/data").exists():
        return True
    return False


def _base_data_dir(chain_id: int) -> Path:
    """Resolve the root data directory for all Animica assets.

    Priority:
    1. ANIMICA_DATA_DIR (expanded with ~)
    2. Docker defaults (/data/chain-<id>)
    3. $HOME/.animica (respecting HOME env var)
    4. ~/.animica (fallback)
    
    Note: Uses $HOME environment variable when available to ensure correct
    behavior in Docker containers where HOME may be set to /data but the
    passwd file still points to /root.
    """

    override = os.environ.get("ANIMICA_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if _is_docker_env():
        return Path(f"/data/chain-{chain_id}")
    
    # Use $HOME environment variable if set, falling back to ~ expansion
    # This ensures correct behavior in Docker where HOME=/data but passwd says /root
    home = os.environ.get("HOME")
    if home:
        return Path(home) / ".animica"
    
    return Path("~/.animica").expanduser()


def get_chain_data_dir(
    chain_id: int,
    base_dir: Path | None = None,
    *,
    create: bool = False,
) -> Path:
    """Resolve the per-chain data directory.

    If base_dir (or ANIMICA_DATA_DIR) already ends with chain-<id>, use it when the
    chain matches; otherwise treat the parent as the root and resolve the requested
    chain directory beneath it.
    """
    base = (base_dir or _base_data_dir(chain_id)).expanduser()
    if base.name.startswith("chain-"):
        try:
            existing_id = int(base.name.split("chain-", 1)[1])
        except (ValueError, IndexError):
            existing_id = None
        if existing_id == chain_id:
            data_dir = base
        else:
            data_dir = base.parent / f"chain-{chain_id}"
    else:
        data_dir = base / f"chain-{chain_id}"
    if create:
        data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_chain_db_path(
    chain_id: int,
    *,
    base_dir: Path | None = None,
    db_name: str = "animica.db",
    create: bool = False,
) -> Path:
    """Return the SQLite DB path for a chain (animica.db by default)."""
    data_dir = get_chain_data_dir(chain_id, base_dir=base_dir, create=create)
    return data_dir / db_name


def _network_data_dir(chain_id: int) -> str:
    """Return the canonical data directory for a chain id."""

    return str(get_chain_data_dir(chain_id, create=False))

logger = logging.getLogger(__name__)

ENV_FILE_VAR = "ANIMICA_ENV_FILE"
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
_DOTENV_VALUES: dict[str, str] = {}
_DOTENV_APPLIED: set[str] = set()
_DOTENV_LOADED = False


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


@dataclass(frozen=True)
class EnvBoolSetting:
    value: bool
    source: str
    raw: Optional[str]


def _parse_dotenv_value(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    for marker in (" #", "\t#"):
        if marker in value:
            value = value.split(marker, 1)[0].rstrip()
    return value


def _resolve_env_file_path(env_path: Optional[Path] = None) -> Optional[Path]:
    if env_path:
        return env_path.expanduser()
    configured = os.environ.get(ENV_FILE_VAR)
    if configured:
        return Path(configured).expanduser()
    cwd = Path.cwd() / ".env"
    if cwd.exists():
        return cwd
    repo_root = Path(__file__).resolve().parents[2]
    root_env = repo_root / ".env"
    if root_env.exists():
        return root_env
    return None


def load_dotenv(
    env_path: Optional[Path] = None,
    *,
    override: bool = False,
    force: bool = False,
) -> dict[str, str]:
    """Load environment variables from a dotenv file into process env."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED and not force:
        return _DOTENV_VALUES
    _DOTENV_VALUES.clear()
    _DOTENV_APPLIED.clear()
    _DOTENV_LOADED = True
    env_file = _resolve_env_file_path(env_path)
    if not env_file or not env_file.exists():
        return _DOTENV_VALUES
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = _parse_dotenv_value(raw_value)
        _DOTENV_VALUES[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
            _DOTENV_APPLIED.add(key)
    return _DOTENV_VALUES


def parse_env_bool(
    value: str | None,
    default: bool = False,
    *,
    name: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> bool:
    """
    Parse a boolean string with robust false handling.

    Accepts true values: 1, true, yes, on.
    Accepts false values: 0, false, no, off.
    """
    if value is None:
        return default
    cleaned = value.strip()
    if not cleaned:
        return default
    normalized = cleaned.lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    if log is not None and name:
        log.warning(
            "Invalid value for %s: '%s' (expected one of %s or %s). Using default=%s.",
            name,
            value,
            ", ".join(sorted(TRUE_VALUES)),
            ", ".join(sorted(FALSE_VALUES)),
            default,
        )
    return default


def resolve_env_bool(
    env_var: str,
    default: bool = False,
    *,
    allow_dotenv: bool = True,
) -> EnvBoolSetting:
    if allow_dotenv:
        load_dotenv()
    raw = os.getenv(env_var)
    if raw is None:
        return EnvBoolSetting(default, "default", None)
    source = "dotenv" if env_var in _DOTENV_APPLIED else "process_env"
    value = parse_env_bool(raw, default, name=env_var, log=logger)
    return EnvBoolSetting(value, source, raw)


def resolve_bootstrap_mode(cli_value: Optional[bool] = None) -> EnvBoolSetting:
    if cli_value is not None:
        raw = "true" if cli_value else "false"
        return EnvBoolSetting(cli_value, "cli_flag", raw)
    return resolve_env_bool("ANIMICA_BOOTSTRAP_NODE", default=False)


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
    return parse_env_bool(value, default, name=env_var, log=logger)


def get_network_defaults(network: str) -> dict[str, any]:
    """
    Get default configuration values for a specific network.
    
    Returns a dictionary with network-specific defaults including:
    - chain_id: Network chain ID (sourced from network_manifest)
    - rpc_url: Default RPC endpoint URL
    - rpc_port: Default RPC port
    - p2p_port: Default P2P port
    - metrics_port: Default metrics port
    - compose_file: Path to network-specific Docker Compose file
    - genesis_path: Path to genesis file (sourced from network_manifest)
    - data_dir: Network-specific data directory
    - db_name: Database file name
    
    Note: devnet and local-devnet are distinct networks:
    - devnet: Uses ops/docker/docker-compose.devnet.yml (full stack with monitoring)
    - local-devnet: Uses tests/devnet/docker-compose.yml (minimal multi-node setup)
    
    Network identity (chain_id, genesis_path) is sourced from core.network_manifest
    to ensure consistency across all components.
    """
    # Get repository root (3 levels up from this file)
    repo_root = Path(__file__).resolve().parents[2]
    
    # Get canonical chain_id and genesis_path from network_manifest
    try:
        from core.network_manifest import get_manifest
        manifest = get_manifest(network=network)
        if manifest:
            canonical_chain_id = manifest.chain_id
            canonical_genesis_path = str(manifest.genesis_path)
        else:
            # Fallback for unknown networks (shouldn't happen in practice)
            canonical_chain_id = 0
            canonical_genesis_path = str(repo_root / "core" / "genesis" / "mainnet.json")
            logger.warning(f"Network '{network}' not found in network_manifest, using mainnet defaults")
    except ImportError:
        # Fallback if network_manifest not available (shouldn't happen)
        logger.warning("core.network_manifest not available, using hardcoded chain_id values")
        canonical_chain_id = {"mainnet": 0, "testnet": 2, "devnet": 1337, "local-devnet": 1337}.get(network, 0)
        canonical_genesis_path = str(repo_root / "core" / "genesis" / f"{network.replace('local-', '')}.json")
    
    network_configs = {
        "mainnet": {
            "chain_id": canonical_chain_id,
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "bootstrap_url": "http://127.0.0.1:8545/rpc",
            "rpc_port": 8545,
            "p2p_port": 30333,
            "metrics_port": 9000,
            "compose_file": repo_root / "ops" / "docker" / "docker-compose.mainnet.yml",
            "genesis_path": canonical_genesis_path,
            "data_dir": _network_data_dir(canonical_chain_id),
            "db_name": "animica.db",
        },
        "testnet": {
            "chain_id": canonical_chain_id,
            "rpc_url": "http://127.0.0.1:18546/rpc",
            "bootstrap_url": "https://rpc.testnet.animica.org/rpc",
            "rpc_port": 18546,
            "p2p_port": 31334,
            "metrics_port": 19000,
            "compose_file": repo_root / "ops" / "docker" / "docker-compose.testnet.yml",
            "genesis_path": canonical_genesis_path,
            "data_dir": _network_data_dir(canonical_chain_id),
            "db_name": "animica.db",
        },
        "devnet": {
            "chain_id": canonical_chain_id,
            "rpc_url": "http://127.0.0.1:28545/rpc",
            "bootstrap_url": "http://127.0.0.1:28545/rpc",
            "rpc_port": 28545,
            "p2p_port": 31335,
            "metrics_port": 29000,
            "compose_file": repo_root / "ops" / "docker" / "docker-compose.devnet.yml",
            "genesis_path": canonical_genesis_path,
            "data_dir": _network_data_dir(canonical_chain_id),
            "db_name": "animica.db",
        },
        "local-devnet": {
            "chain_id": canonical_chain_id,
            "rpc_url": "http://127.0.0.1:38545/rpc",
            "bootstrap_url": "http://127.0.0.1:38545/rpc",
            "rpc_port": 38545,
            "p2p_port": 31336,
            "metrics_port": 39000,
            "compose_file": repo_root / "tests" / "devnet" / "docker-compose.yml",
            "genesis_path": canonical_genesis_path,
            "data_dir": _network_data_dir(canonical_chain_id),
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
    load_dotenv()
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

    bootstrap_setting = resolve_env_bool("ANIMICA_BOOTSTRAP_NODE", False)
    is_bootstrap_node = bootstrap_setting.value

    bootstrap_url = os.getenv("ANIMICA_BOOTSTRAP_RPC_URL")
    if bootstrap_url is not None:
        bootstrap_url = bootstrap_url.strip()
    if not bootstrap_url:
        if is_bootstrap_node:
            bootstrap_url = ""
        else:
            bootstrap_url = defaults.get("bootstrap_url", defaults["rpc_url"])
    chain_id = _safe_int_from_env("ANIMICA_CHAIN_ID", defaults["chain_id"])
    
    # CRITICAL: Validate chain_id matches network using network_manifest
    # This prevents silent misconfigurations where networks run with wrong chain_id
    try:
        from core.network_manifest import get_manifest
        manifest = get_manifest(network=network_name)
        if manifest:
            expected_chain_id = manifest.chain_id
            if chain_id != expected_chain_id:
                error_msg = (
                    f"FATAL: Network '{network_name}' MUST use chain_id={expected_chain_id}, "
                    f"but got chain_id={chain_id}. "
                    f"This indicates a configuration error. Please check ANIMICA_CHAIN_ID environment variable "
                    f"and ensure it matches the network's canonical chain_id from network_manifest."
                )
                logger.error(error_msg)
                raise ValueError(error_msg)
            logger.debug(f"Validated chain_id={chain_id} matches network '{network_name}' (from network_manifest)")
    except ImportError:
        # Fallback validation if network_manifest not available
        logger.warning("core.network_manifest not available, using hardcoded chain_id validation")
        if network_name.lower() == "mainnet" and chain_id != 0:
            error_msg = (
                f"FATAL: Network 'mainnet' MUST use chain_id=0, but got chain_id={chain_id}. "
                f"This indicates a configuration error. Please check ANIMICA_CHAIN_ID environment variable "
                f"and ensure it is not set to a non-zero value when running mainnet."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        if network_name.lower() == "testnet" and chain_id != 2:
            logger.warning(
                f"Network 'testnet' typically uses chain_id=2, but got chain_id={chain_id}. "
                f"This may cause compatibility issues."
            )

    data_dir = str(get_chain_data_dir(chain_id, create=True))

    return NetworkConfig(
        name=network_name,
        rpc_url=rpc_url,
        bootstrap_url=bootstrap_url,
        chain_id=chain_id,
        compose_file=defaults["compose_file"],
        genesis_path=defaults["genesis_path"],
        data_dir=data_dir,
        rpc_port=defaults["rpc_port"],
        db_name=defaults["db_name"],
    )


__all__ = [
    "EnvBoolSetting",
    "NetworkConfig",
    "load_network_config",
    "get_network_defaults",
    "get_chain_data_dir",
    "get_chain_db_path",
    "DEFAULT_NETWORK",
    "DEFAULT_RPC_URL",
    "ENV_FILE_VAR",
    "load_dotenv",
    "parse_env_bool",
    "resolve_env_bool",
    "resolve_bootstrap_mode",
]
