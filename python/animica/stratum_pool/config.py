from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from animica.config import load_network_config

VALID_POOL_MODES = {"pps", "solo", "both"}
VALID_POOL_PROFILES = {"hashshare", "asic_sha256"}


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
    rpc_timeout: float = 300.0
    # Micro-shares floor: 1e-5 of θ. Lets very low hashrate miners
    # (small CPUs, single GPUs, mobile probes) submit shares often
    # enough to get a steady PPS payout instead of going minutes
    # between accepted shares. Per-session vardiff still clamps each
    # client into [min_difficulty, max_difficulty]; the max stays at
    # 1.0 (= θ) for the block-finder share. Operators who want the
    # old conservative floor can set ANIMICA_STRATUM_MIN_DIFFICULTY.
    min_difficulty: float = 0.00001
    max_difficulty: float = 1.0
    poll_interval: float = 1.0
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8550
    network: str = "mainnet"
    profile: str = "hashshare"
    extranonce2_size: int = 4
    pool_mode: str = "pps"
    payout_interval_seconds: float = 0.0
    payout_min_amount: int = 1
    payout_wallet: str = ""
    # Pool-level miner-version enforcement (policy only — never touches consensus).
    # When require_min_version is set and min_miner_version is non-empty, miners
    # reporting an older (or no) version are rejected: their shares and AICF jobs
    # are refused until they update. Reversible by clearing the flag.
    min_miner_version: str = ""
    require_min_version: bool = False
    # ENA training-treasury fee: route this fraction (in basis points) of each
    # accepted share's ANM credit to the ENA training treasury. The miner keeps
    # the remainder. 0 disables the fee. The treasury accrues as an ordinary
    # credited balance and is paid out by the normal payout scheduler.
    ena_fee_bps: int = 0
    ena_treasury_address: str = ""


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val


def _as_bool(override: Any, env_val: Optional[str]) -> bool:
    if override is not None:
        return bool(override)
    return str(env_val or "").strip().lower() in ("1", "true", "yes", "on")


def load_config_from_env(*, overrides: Optional[dict] = None) -> PoolConfig:
    """
    Build a PoolConfig from environment variables with optional overrides.
    """

    overrides = overrides or {}

    network_cfg = load_network_config()

    stratum_bind = overrides.get("stratum_bind") or _env("ANIMICA_STRATUM_BIND")
    if stratum_bind:
        host, port_str = stratum_bind.rsplit(":", 1)
        port = int(port_str)
    else:
        host = overrides.get("host") or _env("ANIMICA_STRATUM_HOST", "0.0.0.0")
        port = int(overrides.get("port") or _env("ANIMICA_STRATUM_PORT", "3333"))

    rpc_url = overrides.get("rpc_url") or _env("ANIMICA_RPC_URL", network_cfg.rpc_url)
    db_url = overrides.get("db_url") or _env(
        "ANIMICA_MINING_POOL_DB_URL", "sqlite:///animica_pool.db"
    )
    chain_id = int(overrides.get("chain_id") or _env("ANIMICA_CHAIN_ID", "1"))
    pool_address = overrides.get("pool_address") or _env("ANIMICA_POOL_ADDRESS", "")
    rpc_timeout = float(
        overrides.get("rpc_timeout")
        or _env("ANIMICA_STRATUM_RPC_TIMEOUT")
        or _env("ANIMICA_RPC_TIMEOUT", "15.0")
    )
    network = overrides.get("network") or _env("ANIMICA_NETWORK", network_cfg.name)

    min_difficulty = float(
        overrides.get("min_difficulty")
        or _env("ANIMICA_STRATUM_MIN_DIFFICULTY", "0.00001")
    )
    max_difficulty = float(
        overrides.get("max_difficulty") or _env("ANIMICA_STRATUM_MAX_DIFFICULTY", "1.0")
    )
    poll_interval = float(
        overrides.get("poll_interval") or _env("ANIMICA_STRATUM_POLL_INTERVAL", "1.0")
    )
    log_level = (
        overrides.get("log_level")
        or _env(
            "ANIMICA_MINING_POOL_LOG_LEVEL", _env("ANIMICA_STRATUM_LOG_LEVEL", "INFO")
        )
    ).upper()
    api_bind = overrides.get("api_bind") or _env("ANIMICA_POOL_API_BIND")
    if api_bind:
        api_host, api_port_str = api_bind.split(":")
        api_port = int(api_port_str)
    else:
        api_host = overrides.get("api_host") or _env("ANIMICA_STRATUM_API_HOST", host)
        api_port = int(
            overrides.get("api_port") or _env("ANIMICA_STRATUM_API_PORT", "8550")
        )

    profile = overrides.get("profile") or _env("ANIMICA_POOL_PROFILE", "hashshare")
    extranonce2_size = int(
        overrides.get("extranonce2_size")
        or _env("ANIMICA_STRATUM_EXTRANONCE2_SIZE", "4")
    )
    pool_mode = (
        str(overrides.get("pool_mode") or _env("ANIMICA_POOL_MODE", "pps"))
        .strip()
        .lower()
    )
    payout_interval_seconds = float(
        overrides.get("payout_interval_seconds")
        or _env("ANIMICA_POOL_PAYOUT_INTERVAL_SECONDS", "0")
    )
    payout_min_amount = int(
        overrides.get("payout_min_amount")
        or _env("ANIMICA_POOL_PAYOUT_MIN_AMOUNT", "1")
    )
    payout_wallet = str(
        overrides.get("payout_wallet")
        or _env("ANIMICA_POOL_PAYOUT_WALLET", pool_address)
        or ""
    ).strip()
    min_miner_version = str(
        overrides.get("min_miner_version")
        or _env("ANIMICA_POOL_MIN_MINER_VERSION", "")
        or ""
    ).strip()
    require_min_version = _as_bool(
        overrides.get("require_min_version"),
        _env("ANIMICA_POOL_REQUIRE_MIN_VERSION", "false"),
    )
    ena_fee_bps = int(
        overrides.get("ena_fee_bps")
        or _env("ANIMICA_POOL_ENA_FEE_BPS", "0")
    )
    ena_treasury_address = str(
        overrides.get("ena_treasury_address")
        or _env("ANIMICA_POOL_ENA_TREASURY_ADDRESS", "")
        or ""
    ).strip()

    if not str(host or "").strip():
        raise ValueError("host must be non-empty")
    if port <= 0 or port > 65535:
        raise ValueError("port must be between 1 and 65535")
    if not rpc_url:
        raise ValueError("rpc_url is required")
    if rpc_timeout <= 0:
        raise ValueError("rpc_timeout must be positive")
    if not pool_address.strip():
        raise ValueError(
            "pool_address is required (set ANIMICA_POOL_ADDRESS or pass --pool-address)"
        )
    if min_difficulty <= 0:
        raise ValueError("min_difficulty must be positive")
    if max_difficulty <= 0:
        raise ValueError("max_difficulty must be positive")
    # Keep order validation for same-unit inputs:
    # - both <= 1.0: legacy ratio mode
    # - both  > 1.0: absolute theta-micro mode
    # Mixed units are normalized at runtime against live θ and are allowed.
    if (
        (min_difficulty <= 1.0 and max_difficulty <= 1.0)
        or (min_difficulty > 1.0 and max_difficulty > 1.0)
    ) and max_difficulty < min_difficulty:
        raise ValueError("max_difficulty must be >= min_difficulty")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    if api_port <= 0 or api_port > 65535:
        raise ValueError("api_port must be between 1 and 65535")
    if profile not in VALID_POOL_PROFILES:
        raise ValueError(
            f"profile must be one of {', '.join(sorted(VALID_POOL_PROFILES))}"
        )
    if pool_mode not in VALID_POOL_MODES:
        raise ValueError(
            f"pool_mode must be one of {', '.join(sorted(VALID_POOL_MODES))}"
        )
    if payout_interval_seconds < 0:
        raise ValueError("payout_interval_seconds must be >= 0")
    if payout_min_amount <= 0:
        raise ValueError("payout_min_amount must be positive")
    if payout_interval_seconds > 0 and not payout_wallet:
        raise ValueError(
            "payout_wallet is required when payout_interval_seconds is enabled"
        )
    if ena_fee_bps < 0 or ena_fee_bps > 10_000:
        raise ValueError("ena_fee_bps must be between 0 and 10000 (basis points)")
    if ena_fee_bps > 0 and not ena_treasury_address:
        raise ValueError(
            "ena_treasury_address is required when ena_fee_bps > 0 "
            "(set ANIMICA_POOL_ENA_TREASURY_ADDRESS)"
        )

    return PoolConfig(
        host=host,
        port=port,
        rpc_url=rpc_url,
        db_url=db_url,
        chain_id=chain_id,
        pool_address=pool_address,
        rpc_timeout=rpc_timeout,
        min_difficulty=min_difficulty,
        max_difficulty=max_difficulty,
        poll_interval=poll_interval,
        log_level=log_level,
        api_host=api_host,
        api_port=api_port,
        network=network,
        profile=profile,
        extranonce2_size=extranonce2_size,
        pool_mode=pool_mode,
        payout_interval_seconds=payout_interval_seconds,
        payout_min_amount=payout_min_amount,
        payout_wallet=payout_wallet,
        min_miner_version=min_miner_version,
        require_min_version=require_min_version,
        ena_fee_bps=ena_fee_bps,
        ena_treasury_address=ena_treasury_address,
    )
