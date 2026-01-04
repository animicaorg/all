"""Configuration models for the Animica GUI miner.

Single source of truth for all miner settings, using Pydantic for validation
and JSON schema generation. Shared by CLI and GUI.
"""

from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


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


class NetworkConfig(BaseModel):
    """Network and RPC configuration."""
    network_type: NetworkType = Field(default=NetworkType.DEVNET, description="Network type")
    rpc_url: str = Field(default="http://127.0.0.1:8545", description="RPC endpoint URL")
    custom_rpc_url: Optional[str] = Field(default=None, description="Custom RPC URL (when network_type=custom)")
    chain_id: Optional[int] = Field(default=None, ge=1, description="Chain ID (None=auto-detect)")


class MinerConfig(BaseModel):
    """Core miner settings."""
    mining_mode: MiningMode = Field(default=MiningMode.SOLO, description="Mining mode")
    payout_address: str = Field(default="", description="Payout address for mining rewards")
    auto_start: bool = Field(default=False, description="Auto-start mining on launch")
    auto_restart_on_crash: bool = Field(default=True, description="Auto-restart miner on crash")


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


def load_config(path: Optional[Path] = None) -> MiningAppConfig:
    """Load configuration from file or create default."""
    if path is None:
        path = get_default_config_path()
    
    if path.exists():
        return MiningAppConfig.from_file(path)
    else:
        # Return default config
        return MiningAppConfig()


def save_config(config: MiningAppConfig, path: Optional[Path] = None) -> None:
    """Save configuration to file."""
    if path is None:
        path = get_default_config_path()
    config.to_file(path)
