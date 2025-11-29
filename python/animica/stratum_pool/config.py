from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class PoolConfig:
    """
    Configuration for the Stratum pool backend.
    """

    host: str = "0.0.0.0"
    port: int = 3333
    rpc_url: str = "http://127.0.0.1:8545/rpc"
    chain_id: int = 1
    pool_address: str = ""
    min_difficulty: float = 0.01
    max_difficulty: float = 1.0
    poll_interval: float = 1.0
    log_level: str = "INFO"


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val


def load_config_from_env(*, overrides: Optional[dict] = None) -> PoolConfig:
    """
    Build a PoolConfig from environment variables with optional overrides.
    """

    overrides = overrides or {}

    host = overrides.get("host") or _env("ANIMICA_STRATUM_HOST", "0.0.0.0")
    port = int(overrides.get("port") or _env("ANIMICA_STRATUM_PORT", "3333"))
    rpc_url = overrides.get("rpc_url") or _env("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")
    chain_id = int(overrides.get("chain_id") or _env("ANIMICA_CHAIN_ID", "1"))
    pool_address = overrides.get("pool_address") or _env("ANIMICA_POOL_ADDRESS", "")

    min_difficulty = float(overrides.get("min_difficulty") or _env("ANIMICA_STRATUM_MIN_DIFFICULTY", "0.01"))
    max_difficulty = float(overrides.get("max_difficulty") or _env("ANIMICA_STRATUM_MAX_DIFFICULTY", "1.0"))
    poll_interval = float(overrides.get("poll_interval") or _env("ANIMICA_STRATUM_POLL_INTERVAL", "1.0"))
    log_level = (overrides.get("log_level") or _env("ANIMICA_STRATUM_LOG_LEVEL", "INFO")).upper()

    if min_difficulty <= 0:
        raise ValueError("min_difficulty must be positive")
    if max_difficulty < min_difficulty:
        raise ValueError("max_difficulty must be >= min_difficulty")

    return PoolConfig(
        host=host,
        port=port,
        rpc_url=rpc_url,
        chain_id=chain_id,
        pool_address=pool_address,
        min_difficulty=min_difficulty,
        max_difficulty=max_difficulty,
        poll_interval=poll_interval,
        log_level=log_level,
    )
