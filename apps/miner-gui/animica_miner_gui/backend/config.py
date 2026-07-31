"""Configuration models for the Animica GUI miner.

Single source of truth for all miner settings, using Pydantic for validation
and JSON schema generation. Shared by CLI and GUI.
"""

from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator

from animica_miner_gui.backend.app_paths import is_frozen


class NetworkType(str, Enum):
    """Supported network types."""
    MAINNET = "mainnet"
    TESTNET = "testnet"
    DEVNET = "devnet"
    CUSTOM = "custom"


class DeviceType(str, Enum):
    """Mining device types."""
    CPU = "cpu"
    GPU = "gpu"
    ASIC = "asic"


class MiningMode(str, Enum):
    """Mining operation modes."""
    SOLO = "solo"
    STRATUM = "stratum"


class LogLevel(str, Enum):
    """Logging levels."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CPUConfig(BaseModel):
    """CPU mining configuration."""
    enabled: bool = Field(default=True, description="Enable CPU mining")
    threads: int = Field(default=0, ge=0, description="Number of threads (0=auto-detect)")
    affinity: Optional[List[int]] = Field(default=None, description="CPU affinity list")
    hugepages: bool = Field(default=False, description="Enable hugepages if available")
    priority: int = Field(default=0, ge=-20, le=19, description="Process priority")

    @field_validator("threads")
    @classmethod
    def validate_threads(cls, v: int) -> int:
        if v == 0:
            return os.cpu_count() or 1
        return v


class GPUConfig(BaseModel):
    """GPU device configuration."""
    device_id: int = Field(default=0, ge=0, description="GPU device ID")
    enabled: bool = Field(default=True, description="Enable this GPU")
    intensity: float = Field(default=1.0, ge=0.0, le=1.0, description="Mining intensity (0-1)")
    worksize: int = Field(default=256, ge=1, description="Work group size")
    name: Optional[str] = Field(default=None, description="Device name")
    vendor: Optional[str] = Field(default=None, description="Device vendor")


class ASICConfig(BaseModel):
    """ASIC worker configuration (stub)."""
    enabled: bool = Field(default=False, description="Enable ASIC worker")
    endpoint: str = Field(default="", description="ASIC endpoint URL")
    worker_name: str = Field(default="", description="Worker name")
    env_template: str = Field(default="hash_worker.asic.env.example", description="Environment template file")
    power_limit: Optional[int] = Field(default=None, ge=0, description="Power limit in watts (placeholder)")


class PoolConfig(BaseModel):
    """Pool configuration for Stratum mode."""
    enabled: bool = Field(default=False, description="Enable pool mining")
    url: str = Field(default="", description="Pool stratum URL")
    username: str = Field(default="", description="Pool username/wallet")
    password: str = Field(default="x", description="Pool password")
    failover_pools: List[Dict[str, str]] = Field(default_factory=list, description="Failover pool configurations")


#: Public JSON-RPC endpoints used when no local node is running.
#:
#: A packaged build ships without a node payload, so before this existed the
#: ONLY code that ever set an RPC URL was NodeController starting a local node
#: — which can never happen in a release build. Every surface therefore had
#: rpc_url=None forever: the wallet showed "--" for balance and nonce, the
#: dashboard had no chain data, and the app reported "RPC unavailable". The
#: public endpoint needs no token and serves the same JSON-RPC.
DEFAULT_RPC_ENDPOINTS: Dict[NetworkType, str] = {
    NetworkType.MAINNET: "https://rpc.animica.org/rpc",
    NetworkType.DEVNET: "http://127.0.0.1:8545/rpc",
}


class NetworkConfig(BaseModel):
    """Network and RPC configuration."""
    # Mainnet, not devnet: this is a miner people download and point at the
    # live chain. Defaulting to devnet meant a fresh install targeted a network
    # that does not exist on the user's machine.
    network_type: NetworkType = Field(default=NetworkType.MAINNET, description="Network type")
    rpc_url: Optional[str] = Field(default=None, description="Explicit RPC endpoint override (None = use the network default)")
    chain_id: Optional[int] = Field(default=None, ge=1, description="Chain ID (None=auto-detect)")

    @field_validator("rpc_url", mode="before")
    @classmethod
    def normalize_optional_url(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    def resolved_rpc_url(self) -> Optional[str]:
        """Return the effective RPC URL: explicit override, else network default.

        Falling back matters because `rpc_url` is only ever populated by a
        locally-started node. Without the fallback a packaged build — which has
        no node to start — has no endpoint at all.
        """
        if self.rpc_url:
            return self.rpc_url
        return DEFAULT_RPC_ENDPOINTS.get(self.network_type)


class MinerConfig(BaseModel):
    """Core miner settings."""
    mining_mode: MiningMode = Field(default=MiningMode.SOLO, description="Mining mode")
    payout_address: str = Field(default="", description="Payout address for mining rewards")
    wallet_file: Optional[str] = Field(default=None, description="Custom wallet file location (default: ~/.animica/wallets.json)")
    auto_start: bool = Field(default=False, description="Auto-start mining on launch")
    auto_restart_on_crash: bool = Field(default=True, description="Auto-restart miner on crash")
    blocks_per_batch: int = Field(default=10, ge=1, le=100, description="Number of blocks to mine per batch (default: 10)")
    retry_delay: float = Field(default=2.0, ge=0.1, le=60.0, description="Delay in seconds before retrying on error (default: 2.0)")


class UIConfig(BaseModel):
    """UI preferences."""
    dark_theme: bool = Field(default=True, description="Use dark theme")
    show_system_tray: bool = Field(default=True, description="Show system tray icon")
    minimize_to_tray: bool = Field(default=True, description="Minimize to tray instead of closing")
    notifications_enabled: bool = Field(default=True, description="Enable notifications")


class SafeModeConfig(BaseModel):
    """Safe mode settings for constrained environments."""
    enabled: bool = Field(default=False, description="Enable safe mode (lower intensity)")
    cpu_limit_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="CPU usage threshold to trigger safe mode")
    ram_limit_gb: Optional[float] = Field(default=None, ge=0.0, description="RAM limit in GB")


class MiningAppConfig(BaseModel):
    """Complete mining application configuration.
    
    This is the single source of truth for all miner settings.
    """
    version: str = Field(default="1.0", description="Config schema version")
    
    # Core sections
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    miner: MinerConfig = Field(default_factory=MinerConfig)
    cpu: CPUConfig = Field(default_factory=CPUConfig)
    gpus: List[GPUConfig] = Field(default_factory=list, description="GPU devices")
    asic: ASICConfig = Field(default_factory=ASICConfig)
    pool: PoolConfig = Field(default_factory=PoolConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    safe_mode: SafeModeConfig = Field(default_factory=SafeModeConfig)
    
    # Logging
    log_level: LogLevel = Field(default=LogLevel.INFO, description="Logging level")

    _load_warnings: List[str] = PrivateAttr(default_factory=list)
    
    @classmethod
    def get_schema_json(cls) -> str:
        """Export JSON schema for validation."""
        return json.dumps(cls.model_json_schema(), indent=2)
    
    @classmethod
    def from_file(cls, path: Path) -> "MiningAppConfig":
        """Load configuration from JSON file."""
        with open(path, "r") as f:
            data = json.load(f)
        return cls(**data)
    
    def to_file(self, path: Path) -> None:
        """Save configuration to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.model_dump(), f, indent=2)
        # Secure config file (0600)
        os.chmod(path, 0o600)
    
    def validate_payout_address(self) -> bool:
        """Validate payout address format."""
        addr = self.miner.payout_address
        if not addr:
            return False
        # Basic validation: should start with 'anim1' and be bech32-ish
        return addr.startswith("anim1") and len(addr) >= 42


def get_default_config_dir() -> Path:
    """Get default configuration directory."""
    return Path.home() / ".animica" / "gui-miner"


def get_default_config_path() -> Path:
    """Get default configuration file path."""
    return get_default_config_dir() / "config.json"


def migrate_config_dict(raw: Any) -> Tuple[Dict[str, Any], bool, List[str]]:
    """Migrate raw config dict into the latest schema."""
    warnings: List[str] = []
    changed = False

    if not isinstance(raw, dict):
        warnings.append("Config root was not a mapping. Replacing with defaults.")
        return {}, True, warnings

    data: Dict[str, Any] = dict(raw)
    network_raw = data.get("network", {})
    if not isinstance(network_raw, dict):
        warnings.append("Config network section was invalid. Resetting network config.")
        network_raw = {}
        changed = True

    network = dict(network_raw)
    rpc_url = network.get("rpc_url")
    external_rpc_url = network.get("external_rpc_url")
    custom_rpc_url = network.get("custom_rpc_url")

    if "mode" in network:
        warnings.append("Removed deprecated external RPC mode configuration.")
        network.pop("mode", None)
        changed = True

    # Older schemas kept the endpoint under different keys. Carry the value
    # over instead of discarding it: these builds ship without a local node, so
    # an external endpoint is the normal configuration, not a deprecated one.
    if external_rpc_url is not None:
        if not rpc_url and isinstance(external_rpc_url, str) and external_rpc_url.strip():
            rpc_url = external_rpc_url.strip()
            warnings.append("Moved external_rpc_url into rpc_url.")
        network.pop("external_rpc_url", None)
        changed = True

    if custom_rpc_url is not None:
        if not rpc_url and isinstance(custom_rpc_url, str) and custom_rpc_url.strip():
            rpc_url = custom_rpc_url.strip()
            warnings.append("Moved custom_rpc_url into rpc_url.")
        network.pop("custom_rpc_url", None)
        changed = True

    if isinstance(rpc_url, str):
        rpc_url = rpc_url.strip() or None

    # NOTE: a remote rpc_url is explicitly SUPPORTED and must be preserved.
    # This used to read:
    #     if rpc_url and not _is_local_rpc_url(rpc_url):
    #         rpc_url = None            # "the GUI now uses the bundled node only"
    # which erased the user's endpoint from disk on every single launch — and
    # popped a "Configuration Repaired" dialog each time — making it impossible
    # to point the app anywhere. Since packaged builds carry no bundled node,
    # that policy left the app with no endpoint at all.

    if network.get("rpc_url") != rpc_url:
        network["rpc_url"] = rpc_url
        changed = True

    # Heal installs created before mainnet became the default. Changing the
    # model default alone is inert on upgrade: anyone who already launched the
    # app has "devnet" written to disk, which resolves to a localhost endpoint
    # a packaged build has no node to serve. Only rewrite when the user has not
    # named their own endpoint, so a real devnet developer is left alone.
    if not rpc_url and network.get("network_type") == NetworkType.DEVNET.value:
        if is_frozen():
            network["network_type"] = NetworkType.MAINNET.value
            warnings.append(
                "Switched from devnet to mainnet: this build has no bundled "
                "node, so devnet had no endpoint to connect to."
            )
            changed = True

    data["network"] = network
    return data, changed, warnings


def load_config(path: Optional[Path] = None) -> MiningAppConfig:
    """Load configuration from file or create default."""
    if path is None:
        path = get_default_config_path()

    raw: Dict[str, Any] = {}
    changed = False
    warnings: List[str] = []

    if path.exists():
        try:
            with open(path, "r") as f:
                raw = json.load(f)
        except json.JSONDecodeError:
            warnings.append("Config file was not valid JSON. Rebuilding with defaults.")
            raw = {}
            changed = True
    else:
        changed = True

    migrated, migrated_changed, migration_warnings = migrate_config_dict(raw)
    warnings.extend(migration_warnings)
    changed = changed or migrated_changed

    try:
        config = MiningAppConfig(**migrated)
    except ValidationError as exc:
        warnings.append(f"Config validation failed: {exc}")
        config = MiningAppConfig()
        changed = True

    if changed:
        try:
            config.to_file(path)
        except Exception:
            warnings.append(f"Failed to write repaired config to {path}")

    config._load_warnings = warnings

    return config


def save_config(config: MiningAppConfig, path: Optional[Path] = None) -> None:
    """Save configuration to file."""
    if path is None:
        path = get_default_config_path()
    config.to_file(path)


def resolve_rpc_url(config_data: Dict[str, Any]) -> Optional[str]:
    """Resolve the effective RPC URL from a serialized config dict.

    Mirrors NetworkConfig.resolved_rpc_url so callers holding a raw dict (the
    miner runner) reach the same endpoint as the ones holding a model.
    """
    network = config_data.get("network") if isinstance(config_data, dict) else None
    if not isinstance(network, dict):
        return DEFAULT_RPC_ENDPOINTS.get(NetworkType.MAINNET)
    explicit = network.get("rpc_url")
    if explicit:
        return explicit
    raw_type = network.get("network_type") or NetworkType.MAINNET
    try:
        net = NetworkType(raw_type)
    except ValueError:
        net = NetworkType.MAINNET
    return DEFAULT_RPC_ENDPOINTS.get(net)


def _is_local_rpc_url(rpc_url: str) -> bool:
    """Return True if RPC URL points to localhost."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(rpc_url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}
