"""
Oracle DA Poster — lightweight utilities and public API.

This package is the thin, import-friendly layer for a data → DA → on-chain
oracle update pipeline. It intentionally keeps zero external runtime deps.

Why this file exists (and why it's non-empty):
  • Provides a stable surface for scripts/tests to import: version, config loader,
    and a simple logger factory.
  • Exposes environment keys and a typed config snapshot (PosterEnv) so callers
    can validate inputs before starting a run loop.
  • Creates a compatibility alias so `python -m oracle_da_poster` (older name)
    still resolves to this package when the template scaffolds `oracle_poster/`.
"""

from __future__ import annotations

import os
import sys
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

# --------------------------------------------------------------------------------------
# Version
# --------------------------------------------------------------------------------------

__all__ = [
    "__version__",
    "ENV_KEYS",
    "PosterEnv",
    "load_env",
    "get_logger",
    "short_help",
]

__version__ = "0.1.0"


# --------------------------------------------------------------------------------------
# Environment keys and typed config
# --------------------------------------------------------------------------------------

ENV_KEYS = (
    # Node / network
    "RPC_URL",
    "WS_URL",
    "CHAIN_ID",
    # Signing / account
    "ORACLE_MNEMONIC",
    "KEYSTORE_PATH",
    "KEYSTORE_PASSWORD",
    "SIGNER_ALG_ID",
    "LOCAL_NONCE_MANAGER",
    "TX_GAS_PRICE_GWEI",
    # Contract
    "ORACLE_CONTRACT_ADDR",
    "ORACLE_ABI_PATH",
    "ORACLE_UPDATE_METHOD",
    # Data Availability
    "DA_API_URL",
    "DA_NAMESPACE_ID",
    "DA_MAX_BLOB_BYTES",
    "BLOB_MIME_TYPE",
    # Source acquisition (choose one per run)
    "SOURCE_FILE_PATH",
    "SOURCE_COMMAND",
    # Runtime behavior
    "POLL_INTERVAL_SEC",
    "POST_IF_CHANGED",
    "MIN_CHANGE_BPS",
    # HTTP / Retry
    "HTTP_TIMEOUT_SEC",
    "RETRY_MAX",
    "RETRY_BACKOFF_INITIAL_MS",
    "RETRY_BACKOFF_MAX_MS",
    # Observability
    "LOG_LEVEL",
    "PROMETHEUS_PORT",
    "METRIC_LABEL_SERVICE",
    "METRIC_LABEL_INSTANCE",
    # CORS / Security knobs for optional servers
    "ALLOW_ORIGINS",
)


@dataclass(frozen=True)
class PosterEnv:
    # --- Node / network ---
    rpc_url: str
    ws_url: Optional[str]
    chain_id: int

    # --- Signing / account ---
    oracle_mnemonic: Optional[str]
    keystore_path: Optional[str]
    keystore_password: Optional[str]
    signer_alg_id: str  # e.g. "dilithium3", "sphincs_shake_128s"
    local_nonce_manager: bool
    tx_gas_price_gwei: Optional[int]

    # --- Contract ---
    oracle_contract_addr: str
    oracle_abi_path: str
    oracle_update_method: str  # e.g. "set_commitment(bytes32,uint64)"

    # --- Data Availability ---
    da_api_url: str
    da_namespace_id: str
    da_max_blob_bytes: int
    blob_mime_type: str

    # --- Source acquisition ---
    source_file_path: Optional[str]
    source_command: Optional[str]

    # --- Runtime behavior ---
    poll_interval_sec: float
    post_if_changed: bool
    min_change_bps: Optional[int]

    # --- HTTP / Retry ---
    http_timeout_sec: float
    retry_max: int
    retry_backoff_initial_ms: int
    retry_backoff_max_ms: int

    # --- Observability ---
    log_level: str
    prometheus_port: Optional[int]
    metric_label_service: Optional[str]
    metric_label_instance: Optional[str]

    # --- Security / CORS ---
    allow_origins: Optional[str]

    # raw dump (for debugging/echo)
    raw: Dict[str, Any]


def _getenv_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def _getenv_int(name: str, default: Optional[int] = None) -> Optional[int]:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return int(val, 10)
    except ValueError as e:
        raise ValueError(f"ENV {name} must be an integer, got {val!r}") from e


def _getenv_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError as e:
        raise ValueError(f"ENV {name} must be a number, got {val!r}") from e


def load_env(strict: bool = True) -> PosterEnv:
    """
    Load environment variables into a typed PosterEnv.

    strict=True will raise ValueError on missing essentials. Essentials:
      RPC_URL, CHAIN_ID, ORACLE_CONTRACT_ADDR, ORACLE_ABI_PATH, ORACLE_UPDATE_METHOD,
      DA_API_URL, DA_NAMESPACE_ID

    Returns:
        PosterEnv
    """
    raw = {k: os.getenv(k) for k in ENV_KEYS}

    def need(name: str) -> str:
        v = os.getenv(name)
        if v is None or v.strip() == "":
            if strict:
                raise ValueError(f"Missing required ENV: {name}")
            return ""
        return v

    # Required core
    rpc_url = need("RPC_URL")
    chain_id = _getenv_int("CHAIN_ID", None)
    if chain_id is None:
        if strict:
            raise ValueError("Missing required ENV: CHAIN_ID")
        chain_id = 0

    oracle_contract_addr = need("ORACLE_CONTRACT_ADDR")
    oracle_abi_path = need("ORACLE_ABI_PATH")
    oracle_update_method = need("ORACLE_UPDATE_METHOD")

    da_api_url = need("DA_API_URL")
    da_namespace_id = need("DA_NAMESPACE_ID")

    # Optional / defaults
    ws_url = os.getenv("WS_URL")
    oracle_mnemonic = os.getenv("ORACLE_MNEMONIC")
    keystore_path = os.getenv("KEYSTORE_PATH")
    keystore_password = os.getenv("KEYSTORE_PASSWORD")
    signer_alg_id = os.getenv("SIGNER_ALG_ID", "dilithium3")

    local_nonce_manager = _getenv_bool("LOCAL_NONCE_MANAGER", False)
    tx_gas_price_gwei = _getenv_int("TX_GAS_PRICE_GWEI", None)

    da_max_blob_bytes = _getenv_int("DA_MAX_BLOB_BYTES", 1024 * 1024) or 0
    blob_mime_type = os.getenv("BLOB_MIME_TYPE", "application/octet-stream")

    source_file_path = os.getenv("SOURCE_FILE_PATH")
    source_command = os.getenv("SOURCE_COMMAND")

    poll_interval_sec = _getenv_float("POLL_INTERVAL_SEC", 10.0)
    post_if_changed = _getenv_bool("POST_IF_CHANGED", True)
    min_change_bps = _getenv_int("MIN_CHANGE_BPS", None)

    http_timeout_sec = _getenv_float("HTTP_TIMEOUT_SEC", 15.0)
    retry_max = _getenv_int("RETRY_MAX", 5) or 5
    retry_backoff_initial_ms = _getenv_int("RETRY_BACKOFF_INITIAL_MS", 250) or 250
    retry_backoff_max_ms = _getenv_int("RETRY_BACKOFF_MAX_MS", 5000) or 5000

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    prometheus_port = _getenv_int("PROMETHEUS_PORT", None)
    metric_label_service = os.getenv("METRIC_LABEL_SERVICE")
    metric_label_instance = os.getenv("METRIC_LABEL_INSTANCE")
    allow_origins = os.getenv("ALLOW_ORIGINS")

    return PosterEnv(
        # network
        rpc_url=rpc_url,
        ws_url=ws_url,
        chain_id=int(chain_id),
        # signing
        oracle_mnemonic=oracle_mnemonic,
        keystore_path=keystore_path,
        keystore_password=keystore_password,
        signer_alg_id=signer_alg_id,
        local_nonce_manager=local_nonce_manager,
        tx_gas_price_gwei=tx_gas_price_gwei,
        # contract
        oracle_contract_addr=oracle_contract_addr,
        oracle_abi_path=oracle_abi_path,
        oracle_update_method=oracle_update_method,
        # DA
        da_api_url=da_api_url,
        da_namespace_id=da_namespace_id,
        da_max_blob_bytes=int(da_max_blob_bytes),
        blob_mime_type=blob_mime_type,
        # source
        source_file_path=source_file_path,
        source_command=source_command,
        # runtime
        poll_interval_sec=float(poll_interval_sec),
        post_if_changed=bool(post_if_changed),
        min_change_bps=min_change_bps,
        # http/retry
        http_timeout_sec=float(http_timeout_sec),
        retry_max=int(retry_max),
        retry_backoff_initial_ms=int(retry_backoff_initial_ms),
        retry_backoff_max_ms=int(retry_backoff_max_ms),
        # observability
        log_level=log_level,
        prometheus_port=prometheus_port,
        metric_label_service=metric_label_service,
        metric_label_instance=metric_label_instance,
        # security
        allow_origins=allow_origins,
        # raw
        raw=raw,
    )


# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------

def get_logger(name: str = "oracle_poster", level: Optional[str] = None) -> logging.Logger:
    """
    Get a structured-ish logger with a sensible default format. Respects LOG_LEVEL.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        # Root-ish configuration only once.
        handler = logging.StreamHandler()
        fmt = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
        logger.addHandler(handler)
    lvl = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    try:
        logger.setLevel(getattr(logging, lvl))
    except AttributeError:
        logger.setLevel(logging.INFO)
    return logger


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def short_help() -> str:
    """
    A brief usage description suitable for CLI --help epilogs.
    """
    return (
        "Oracle DA Poster expects configuration via environment variables. "
        "Provide source data either as a file path (SOURCE_FILE_PATH) or a shell "
        "command that prints bytes to stdout (SOURCE_COMMAND). The poster will: "
        "(1) read/produce bytes, (2) post to DA to get a commitment, "
        "(3) call the oracle contract to update the latest commitment."
    )


# --------------------------------------------------------------------------------------
# Backward-compat import alias
# --------------------------------------------------------------------------------------

# Some templates/tools still execute `python -m oracle_da_poster`. Make that resolve
# to this package module without requiring duplicate directories.
sys.modules.setdefault("oracle_da_poster", sys.modules[__name__])

