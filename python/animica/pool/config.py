"""
Configuration for the PPLNS mining pool.

Extends the existing stratum_pool.PoolConfig with additional pool-specific settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class PoolConfig:
    """
    Complete configuration for the PPLNS mining pool.
    
    Combines Stratum server settings with pool-specific parameters.
    """

    # Stratum server settings
    host: str = "127.0.0.1"  # Default to localhost for security
    port: int = 3333
    rpc_url: str = "http://127.0.0.1:8545/rpc"
    chain_id: int = 1
    
    # Database
    db_path: str = "~/.animica/pool.db"
    
    # Pool identity
    pool_address: str = ""  # Pool fee address (coinbase recipient)
    pool_fee_percent: float = 1.0  # 1% default
    donation_fee_percent: float = 0.0  # Optional dev donation
    
    # PPLNS settings
    mode: str = "pplns"  # pplns, pps (future), solo (future)
    pplns_window_work: int = 2  # Window size as multiple of network difficulty
    
    # Block maturity
    maturity_blocks: int = 20  # Blocks before payout
    
    # Payout settings
    min_payout: int = 1_000_000  # Minimum payout in base units (1 ANM = 1e6)
    payout_interval_sec: int = 600  # 10 minutes
    max_payout_outputs: int = 100  # Max outputs per tx
    
    # VarDiff settings
    vardiff_enabled: bool = True
    vardiff_target_shares_per_min: float = 10.0
    vardiff_retarget_sec: float = 60.0
    vardiff_min_difficulty: float = 0.01
    vardiff_max_difficulty: float = 1.0
    vardiff_variance_percent: float = 30.0  # Allow 30% variance
    
    # Abuse prevention
    ban_threshold_invalid_shares: int = 10  # Invalid shares before temp ban
    ban_duration_sec: int = 3600  # 1 hour
    max_connections_per_ip: int = 10
    rate_limit_auth_per_min: int = 60
    
    # Stats
    hashrate_ema_alpha: float = 0.1  # EMA smoothing factor
    stats_update_interval_sec: int = 60
    
    # API
    api_enabled: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8550
    api_auth_token: Optional[str] = None
    
    # Security
    auth_required: bool = False  # Require auth tokens for miners
    auth_token: Optional[str] = None  # Global auth token
    
    # Logging
    log_level: str = "INFO"
    
    # Advanced
    rpc_timeout: float = 15.0
    poll_interval: float = 1.0  # Job template polling
    job_cache_size: int = 100
    
    # Hot/cold wallet (future)
    hot_wallet_address: Optional[str] = None
    cold_wallet_address: Optional[str] = None


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Get environment variable with default."""
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val


def load_pool_config_from_env(*, overrides: Optional[dict] = None) -> PoolConfig:
    """
    Load pool configuration from environment variables and overrides.
    
    Priority: overrides > environment > defaults
    """
    overrides = overrides or {}

    # Extract values with precedence
    host = overrides.get("host") or _env("ANIMICA_POOL_HOST", "127.0.0.1")
    port = int(overrides.get("port") or _env("ANIMICA_POOL_PORT", "3333"))
    rpc_url = overrides.get("rpc_url") or _env(
        "ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc"
    )
    chain_id = int(overrides.get("chain_id") or _env("ANIMICA_CHAIN_ID", "1"))
    
    db_path = overrides.get("db_path") or _env(
        "ANIMICA_POOL_DB", "~/.animica/pool.db"
    )
    
    pool_address = overrides.get("pool_address") or _env("ANIMICA_POOL_ADDRESS", "")
    pool_fee_percent = float(
        overrides.get("pool_fee_percent") or _env("ANIMICA_POOL_FEE_PERCENT", "1.0")
    )
    donation_fee_percent = float(
        overrides.get("donation_fee_percent")
        or _env("ANIMICA_POOL_DONATION_FEE_PERCENT", "0.0")
    )
    
    mode = overrides.get("mode") or _env("ANIMICA_POOL_MODE", "pplns")
    pplns_window_work = int(
        overrides.get("pplns_window_work") or _env("ANIMICA_POOL_PPLNS_WINDOW", "2")
    )
    
    maturity_blocks = int(
        overrides.get("maturity_blocks") or _env("ANIMICA_POOL_MATURITY_BLOCKS", "20")
    )
    
    min_payout = int(
        overrides.get("min_payout") or _env("ANIMICA_POOL_MIN_PAYOUT", "1000000")
    )
    payout_interval_sec = int(
        overrides.get("payout_interval_sec")
        or _env("ANIMICA_POOL_PAYOUT_INTERVAL", "600")
    )
    max_payout_outputs = int(
        overrides.get("max_payout_outputs")
        or _env("ANIMICA_POOL_MAX_PAYOUT_OUTPUTS", "100")
    )
    
    vardiff_enabled = (
        overrides.get("vardiff_enabled") or _env("ANIMICA_POOL_VARDIFF", "true")
    ).lower() in ("true", "1", "yes")
    vardiff_target_shares_per_min = float(
        overrides.get("vardiff_target_shares_per_min")
        or _env("ANIMICA_POOL_VARDIFF_TARGET", "10.0")
    )
    vardiff_retarget_sec = float(
        overrides.get("vardiff_retarget_sec")
        or _env("ANIMICA_POOL_VARDIFF_RETARGET", "60.0")
    )
    vardiff_min_difficulty = float(
        overrides.get("vardiff_min_difficulty")
        or _env("ANIMICA_POOL_VARDIFF_MIN", "0.01")
    )
    vardiff_max_difficulty = float(
        overrides.get("vardiff_max_difficulty")
        or _env("ANIMICA_POOL_VARDIFF_MAX", "1.0")
    )
    vardiff_variance_percent = float(
        overrides.get("vardiff_variance_percent")
        or _env("ANIMICA_POOL_VARDIFF_VARIANCE", "30.0")
    )
    
    ban_threshold_invalid_shares = int(
        overrides.get("ban_threshold_invalid_shares")
        or _env("ANIMICA_POOL_BAN_THRESHOLD", "10")
    )
    ban_duration_sec = int(
        overrides.get("ban_duration_sec") or _env("ANIMICA_POOL_BAN_DURATION", "3600")
    )
    max_connections_per_ip = int(
        overrides.get("max_connections_per_ip")
        or _env("ANIMICA_POOL_MAX_CONNECTIONS_PER_IP", "10")
    )
    rate_limit_auth_per_min = int(
        overrides.get("rate_limit_auth_per_min")
        or _env("ANIMICA_POOL_RATE_LIMIT_AUTH", "60")
    )
    
    hashrate_ema_alpha = float(
        overrides.get("hashrate_ema_alpha")
        or _env("ANIMICA_POOL_HASHRATE_EMA_ALPHA", "0.1")
    )
    stats_update_interval_sec = int(
        overrides.get("stats_update_interval_sec")
        or _env("ANIMICA_POOL_STATS_INTERVAL", "60")
    )
    
    api_enabled = (
        overrides.get("api_enabled") or _env("ANIMICA_POOL_API_ENABLED", "true")
    ).lower() in ("true", "1", "yes")
    api_host = overrides.get("api_host") or _env("ANIMICA_POOL_API_HOST", "127.0.0.1")
    api_port = int(
        overrides.get("api_port") or _env("ANIMICA_POOL_API_PORT", "8550")
    )
    api_auth_token = overrides.get("api_auth_token") or _env("ANIMICA_POOL_API_TOKEN")
    
    auth_required = (
        overrides.get("auth_required") or _env("ANIMICA_POOL_AUTH_REQUIRED", "false")
    ).lower() in ("true", "1", "yes")
    auth_token = overrides.get("auth_token") or _env("ANIMICA_POOL_AUTH_TOKEN")
    
    log_level = (overrides.get("log_level") or _env("ANIMICA_LOG_LEVEL", "INFO")).upper()
    
    rpc_timeout = float(
        overrides.get("rpc_timeout") or _env("ANIMICA_RPC_TIMEOUT", "15.0")
    )
    poll_interval = float(
        overrides.get("poll_interval") or _env("ANIMICA_POOL_POLL_INTERVAL", "1.0")
    )
    job_cache_size = int(
        overrides.get("job_cache_size") or _env("ANIMICA_POOL_JOB_CACHE_SIZE", "100")
    )
    
    hot_wallet_address = overrides.get("hot_wallet_address") or _env(
        "ANIMICA_POOL_HOT_WALLET"
    )
    cold_wallet_address = overrides.get("cold_wallet_address") or _env(
        "ANIMICA_POOL_COLD_WALLET"
    )

    # Validation
    if not pool_address:
        raise ValueError("pool_address is required (--address or ANIMICA_POOL_ADDRESS)")
    
    if pool_fee_percent < 0 or pool_fee_percent > 100:
        raise ValueError("pool_fee_percent must be between 0 and 100")
    
    if donation_fee_percent < 0 or donation_fee_percent > 100:
        raise ValueError("donation_fee_percent must be between 0 and 100")
    
    if pool_fee_percent + donation_fee_percent > 100:
        raise ValueError("pool_fee_percent + donation_fee_percent cannot exceed 100")
    
    if maturity_blocks < 0:
        raise ValueError("maturity_blocks must be non-negative")
    
    if min_payout < 0:
        raise ValueError("min_payout must be non-negative")
    
    if payout_interval_sec <= 0:
        raise ValueError("payout_interval_sec must be positive")
    
    if vardiff_min_difficulty <= 0:
        raise ValueError("vardiff_min_difficulty must be positive")
    
    if vardiff_max_difficulty < vardiff_min_difficulty:
        raise ValueError("vardiff_max_difficulty must be >= vardiff_min_difficulty")
    
    if auth_required and not auth_token:
        raise ValueError("auth_token required when auth_required=true")

    return PoolConfig(
        host=host,
        port=port,
        rpc_url=rpc_url,
        chain_id=chain_id,
        db_path=db_path,
        pool_address=pool_address,
        pool_fee_percent=pool_fee_percent,
        donation_fee_percent=donation_fee_percent,
        mode=mode,
        pplns_window_work=pplns_window_work,
        maturity_blocks=maturity_blocks,
        min_payout=min_payout,
        payout_interval_sec=payout_interval_sec,
        max_payout_outputs=max_payout_outputs,
        vardiff_enabled=vardiff_enabled,
        vardiff_target_shares_per_min=vardiff_target_shares_per_min,
        vardiff_retarget_sec=vardiff_retarget_sec,
        vardiff_min_difficulty=vardiff_min_difficulty,
        vardiff_max_difficulty=vardiff_max_difficulty,
        vardiff_variance_percent=vardiff_variance_percent,
        ban_threshold_invalid_shares=ban_threshold_invalid_shares,
        ban_duration_sec=ban_duration_sec,
        max_connections_per_ip=max_connections_per_ip,
        rate_limit_auth_per_min=rate_limit_auth_per_min,
        hashrate_ema_alpha=hashrate_ema_alpha,
        stats_update_interval_sec=stats_update_interval_sec,
        api_enabled=api_enabled,
        api_host=api_host,
        api_port=api_port,
        api_auth_token=api_auth_token,
        auth_required=auth_required,
        auth_token=auth_token,
        log_level=log_level,
        rpc_timeout=rpc_timeout,
        poll_interval=poll_interval,
        job_cache_size=job_cache_size,
        hot_wallet_address=hot_wallet_address,
        cold_wallet_address=cold_wallet_address,
    )
