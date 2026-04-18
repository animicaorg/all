from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _bool_env(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BridgeBanmConfig:
    database_url: str
    api_host: str
    api_port: int
    bridge_public_base_url: str
    bridge_admin_token_secret: str

    animica_rpc_url: str
    animica_bridge_custody_address: str
    animica_bridge_custody_key_ref: str
    animica_confirmations_required: int
    animica_release_confirm_policy: str

    evm_rpc_url: str
    evm_chain_id: int
    evm_banm_token_address: str
    evm_bridge_controller_address: str
    evm_bridge_vault_address: str
    evm_bridge_deposit_router_address: str
    evm_bridge_operator_key_ref: str
    evm_operator_private_key: str
    evm_confirmations_required: int

    order_expiry_minutes: int
    enable_claim_code_confirmation: bool
    require_evm_signatures: bool
    enable_animica_signatures_if_available: bool

    bridge_paused: bool
    bridge_paused_forward: bool
    bridge_paused_reverse: bool

    max_order_amount_anm: int
    max_order_amount_banm_wei: int
    min_order_amount_anm: int
    min_order_amount_banm_wei: int
    daily_mint_cap_banm_wei: int
    daily_release_cap_anm: int
    bridge_fee_bps_forward: int
    bridge_fee_bps_reverse: int

    worker_enabled: bool
    worker_poll_interval_seconds: float


def load_config() -> BridgeBanmConfig:
    cfg = BridgeBanmConfig(
        database_url=_env("DATABASE_URL", "postgresql+psycopg://animica:animica@127.0.0.1:5432/banm_bridge")
        or "postgresql+psycopg://animica:animica@127.0.0.1:5432/banm_bridge",
        api_host=_env("BANM_BRIDGE_API_HOST", "0.0.0.0") or "0.0.0.0",
        api_port=int(_env("BANM_BRIDGE_API_PORT", "8660") or "8660"),
        bridge_public_base_url=_env("BANM_BRIDGE_PUBLIC_BASE_URL", "http://localhost:5177")
        or "http://localhost:5177",
        bridge_admin_token_secret=_env("BANM_BRIDGE_ADMIN_TOKEN_SECRET", "change-me-banmm-admin-secret")
        or "change-me-banmm-admin-secret",
        animica_rpc_url=_env("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc") or "http://127.0.0.1:8545/rpc",
        animica_bridge_custody_address=_env("ANIMICA_BRIDGE_CUSTODY_ADDRESS", "") or "",
        animica_bridge_custody_key_ref=_env("ANIMICA_BRIDGE_CUSTODY_KEY_REF", "wallet:bridge-custody") or "wallet:bridge-custody",
        animica_confirmations_required=int(_env("ANIMICA_CONFIRMATIONS_REQUIRED", "6") or "6"),
        animica_release_confirm_policy=_env("ANIMICA_RELEASE_CONFIRM_POLICY", "confirmed") or "confirmed",
        evm_rpc_url=_env("EVM_RPC_URL", "https://data-seed-prebsc-1-s1.binance.org:8545") or "https://data-seed-prebsc-1-s1.binance.org:8545",
        evm_chain_id=int(_env("EVM_CHAIN_ID", "97") or "97"),
        evm_banm_token_address=_env("EVM_BANM_TOKEN_ADDRESS", "0x0000000000000000000000000000000000000000")
        or "0x0000000000000000000000000000000000000000",
        evm_bridge_controller_address=_env("EVM_BRIDGE_CONTROLLER_ADDRESS", "0x0000000000000000000000000000000000000000")
        or "0x0000000000000000000000000000000000000000",
        evm_bridge_vault_address=_env("EVM_BRIDGE_VAULT_ADDRESS", "0x0000000000000000000000000000000000000000")
        or "0x0000000000000000000000000000000000000000",
        evm_bridge_deposit_router_address=_env("EVM_BRIDGE_DEPOSIT_ROUTER_ADDRESS", "0x0000000000000000000000000000000000000000")
        or "0x0000000000000000000000000000000000000000",
        evm_bridge_operator_key_ref=_env("EVM_BRIDGE_OPERATOR_KEY_REF", "env:EVM_OPERATOR_PRIVATE_KEY")
        or "env:EVM_OPERATOR_PRIVATE_KEY",
        evm_operator_private_key=_env("EVM_OPERATOR_PRIVATE_KEY", "") or "",
        evm_confirmations_required=int(_env("EVM_CONFIRMATIONS_REQUIRED", "12") or "12"),
        order_expiry_minutes=int(_env("ORDER_EXPIRY_MINUTES", "30") or "30"),
        enable_claim_code_confirmation=_bool_env("ENABLE_CLAIM_CODE_CONFIRMATION", False),
        require_evm_signatures=_bool_env("REQUIRE_EVM_SIGNATURES", True),
        enable_animica_signatures_if_available=_bool_env("ENABLE_ANIMICA_SIGNATURES_IF_AVAILABLE", False),
        bridge_paused=_bool_env("BRIDGE_PAUSED", False),
        bridge_paused_forward=_bool_env("BRIDGE_PAUSED_FORWARD", False),
        bridge_paused_reverse=_bool_env("BRIDGE_PAUSED_REVERSE", False),
        max_order_amount_anm=int(_env("MAX_ORDER_AMOUNT_ANM", "1000000000000") or "1000000000000"),
        max_order_amount_banm_wei=int(_env("MAX_ORDER_AMOUNT_BANM", "1000000000000000000000000") or "1000000000000000000000000"),
        min_order_amount_anm=int(_env("MIN_ORDER_AMOUNT_ANM", "10000000") or "10000000"),
        min_order_amount_banm_wei=int(_env("MIN_ORDER_AMOUNT_BANM", "10000000000000000") or "10000000000000000"),
        daily_mint_cap_banm_wei=int(_env("DAILY_MINT_CAP_BANM", "10000000000000000000000000") or "10000000000000000000000000"),
        daily_release_cap_anm=int(_env("DAILY_RELEASE_CAP_ANM", "10000000000000") or "10000000000000"),
        bridge_fee_bps_forward=int(_env("BRIDGE_FEE_BPS_FORWARD", "25") or "25"),
        bridge_fee_bps_reverse=int(_env("BRIDGE_FEE_BPS_REVERSE", "25") or "25"),
        worker_enabled=_bool_env("BANM_BRIDGE_WORKER_ENABLED", True),
        worker_poll_interval_seconds=float(_env("BANM_BRIDGE_WORKER_POLL_INTERVAL_SECONDS", "5") or "5"),
    )
    _validate_config(cfg)
    return cfg


def _validate_config(cfg: BridgeBanmConfig) -> None:
    if not cfg.animica_bridge_custody_address.strip():
        raise ValueError("ANIMICA_BRIDGE_CUSTODY_ADDRESS is required")
    if cfg.order_expiry_minutes <= 0:
        raise ValueError("ORDER_EXPIRY_MINUTES must be > 0")
    if cfg.animica_confirmations_required < 1:
        raise ValueError("ANIMICA_CONFIRMATIONS_REQUIRED must be >= 1")
    if cfg.evm_confirmations_required < 1:
        raise ValueError("EVM_CONFIRMATIONS_REQUIRED must be >= 1")
    if not (0 <= cfg.bridge_fee_bps_forward <= 10_000):
        raise ValueError("BRIDGE_FEE_BPS_FORWARD must be within 0..10000")
    if not (0 <= cfg.bridge_fee_bps_reverse <= 10_000):
        raise ValueError("BRIDGE_FEE_BPS_REVERSE must be within 0..10000")
    if cfg.min_order_amount_anm > cfg.max_order_amount_anm:
        raise ValueError("MIN_ORDER_AMOUNT_ANM must be <= MAX_ORDER_AMOUNT_ANM")
    if cfg.min_order_amount_banm_wei > cfg.max_order_amount_banm_wei:
        raise ValueError("MIN_ORDER_AMOUNT_BANM must be <= MAX_ORDER_AMOUNT_BANM")

