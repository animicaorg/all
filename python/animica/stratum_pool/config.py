from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from animica.config import load_network_config


@dataclass
class PoolConfig:
    """
    Configuration for the Stratum pool backend.
    """

    host: str = "0.0.0.0"
    port: int = 3333
    rpc_url: str = "http://127.0.0.1:8545/rpc"
    db_url: str = "sqlite:///animica_pool.db"
    chain_id: int = 1
    pool_address: str = ""
    min_difficulty: float = 0.01
    max_difficulty: float = 1.0
    poll_interval: float = 1.0
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8550
    network: str = "devnet"
    profile: str = "hashshare"
    extranonce2_size: int = 4


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

    network_cfg = load_network_config()

    stratum_bind = overrides.get("stratum_bind") or _env("ANIMICA_STRATUM_BIND")
    if stratum_bind:
        host, port_str = stratum_bind.split(":")
        port = int(port_str)
    else:
        host = overrides.get("host") or _env("ANIMICA_STRATUM_HOST", "0.0.0.0")
        port = int(overrides.get("port") or _env("ANIMICA_STRATUM_PORT", "3333"))

    rpc_url = overrides.get("rpc_url") or _env("ANIMICA_RPC_URL", network_cfg.rpc_url)
    db_url = overrides.get("db_url") or _env("ANIMICA_MINING_POOL_DB_URL", "sqlite:///animica_pool.db")
    chain_id = int(overrides.get("chain_id") or _env("ANIMICA_CHAIN_ID", "1"))
    pool_address = overrides.get("pool_address") or _env("ANIMICA_POOL_ADDRESS", "")
    network = overrides.get("network") or _env("ANIMICA_NETWORK", network_cfg.name)

    min_difficulty = float(overrides.get("min_difficulty") or _env("ANIMICA_STRATUM_MIN_DIFFICULTY", "0.01"))
    max_difficulty = float(overrides.get("max_difficulty") or _env("ANIMICA_STRATUM_MAX_DIFFICULTY", "1.0"))
    poll_interval = float(overrides.get("poll_interval") or _env("ANIMICA_STRATUM_POLL_INTERVAL", "1.0"))
    log_level = (overrides.get("log_level") or _env("ANIMICA_MINING_POOL_LOG_LEVEL", _env("ANIMICA_STRATUM_LOG_LEVEL", "INFO"))).upper()
    api_bind = overrides.get("api_bind") or _env("ANIMICA_POOL_API_BIND")
    if api_bind:
        api_host, api_port_str = api_bind.split(":")
        api_port = int(api_port_str)
    else:
        api_host = overrides.get("api_host") or _env("ANIMICA_STRATUM_API_HOST", host)
        api_port = int(overrides.get("api_port") or _env("ANIMICA_STRATUM_API_PORT", "8550"))

    profile = overrides.get("profile") or _env("ANIMICA_POOL_PROFILE", "hashshare")
    extranonce2_size = int(overrides.get("extranonce2_size") or _env("ANIMICA_STRATUM_EXTRANONCE2_SIZE", "4"))

    if min_difficulty <= 0:
        raise ValueError("min_difficulty must be positive")
    if max_difficulty < min_difficulty:
        raise ValueError("max_difficulty must be >= min_difficulty")

    return PoolConfig(
        host=host,
        port=port,
        rpc_url=rpc_url,
        db_url=db_url,
        chain_id=chain_id,
        pool_address=pool_address,
        min_difficulty=min_difficulty,
        max_difficulty=max_difficulty,
        poll_interval=poll_interval,
        log_level=log_level,
        api_host=api_host,
        api_port=api_port,
        network=network,
        profile=profile,
        extranonce2_size=extranonce2_size,
    )
