"""
Checkpoint configuration loader.

Reads environment variables to configure checkpoint behavior:
- ANIMICA_CHECKPOINTS_MODE: off|rpc|file (default: off)
- ANIMICA_CHECKPOINTS_RPC_URL: URL to fetch checkpoints from (default: http://144.126.133.21:30337/rpc)
- ANIMICA_CHECKPOINTS_FILE: Path to local JSON checkpoint file
- ANIMICA_CHECKPOINTS_MAX_AGE: Maximum age of checkpoints in seconds (optional)
- ANIMICA_CHECKPOINTS_STRICT: If true, fail fast if checkpoints unavailable (default: false)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True, slots=True)
class CheckpointsConfig:
    """Configuration for checkpoint mechanism."""
    
    mode: Literal["off", "rpc", "file"] = "off"
    rpc_url: str = "http://144.126.133.21:30337/rpc"
    file_path: Optional[str] = None
    max_age_seconds: Optional[int] = None
    strict: bool = False
    
    def is_enabled(self) -> bool:
        """Return True if checkpoints are enabled."""
        return self.mode != "off"
    
    def requires_rpc(self) -> bool:
        """Return True if RPC mode is enabled."""
        return self.mode == "rpc"
    
    def requires_file(self) -> bool:
        """Return True if file mode is enabled."""
        return self.mode == "file"


def _getenv(name: str, default: str | None = None) -> str | None:
    """Get environment variable with optional default."""
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip()


def _getenv_bool(name: str, default: bool) -> bool:
    """Get boolean environment variable."""
    v = _getenv(name)
    if v is None:
        return default
    return v.lower() in {"1", "true", "yes", "on"}


def _getenv_int(name: str, default: int | None = None) -> int | None:
    """Get integer environment variable."""
    v = _getenv(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def load_checkpoints_config() -> CheckpointsConfig:
    """
    Load checkpoint configuration from environment variables.
    
    Returns:
        CheckpointsConfig with settings loaded from environment.
    """
    mode_str = _getenv("ANIMICA_CHECKPOINTS_MODE", "off")
    if mode_str not in {"off", "rpc", "file"}:
        # Invalid mode, default to off with warning
        mode_str = "off"
    
    mode = mode_str  # type: ignore[assignment]
    
    rpc_url = _getenv("ANIMICA_CHECKPOINTS_RPC_URL", "http://144.126.133.21:30337/rpc")
    
    file_path = _getenv("ANIMICA_CHECKPOINTS_FILE")
    if file_path:
        file_path = os.path.expanduser(file_path)
    
    max_age_seconds = _getenv_int("ANIMICA_CHECKPOINTS_MAX_AGE")
    
    strict = _getenv_bool("ANIMICA_CHECKPOINTS_STRICT", False)
    
    return CheckpointsConfig(
        mode=mode,
        rpc_url=rpc_url,
        file_path=file_path,
        max_age_seconds=max_age_seconds,
        strict=strict,
    )
