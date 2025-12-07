"""
Billing configuration module.

Loads billing configuration from environment variables with safe defaults.
Supports free/devnet mode (default) and paid mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Literal, Optional


def _getenv(key: str, default: str = "") -> str:
    """Get environment variable with default."""
    return os.getenv(key, default).strip()


def _getenv_int(key: str, default: int) -> int:
    """Get environment variable as int with default."""
    val = _getenv(key)
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _getenv_float(key: str, default: float) -> float:
    """Get environment variable as float with default."""
    val = _getenv(key)
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _getenv_bool(key: str, default: bool) -> bool:
    """Get environment variable as bool with default."""
    val = _getenv(key).lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on", "paid")


@dataclass(frozen=True)
class PlanConfig:
    """
    Configuration for a billing plan.
    
    Attributes:
        name: Plan name (e.g., "free", "pro", "enterprise")
        rate_limit_rpm: Requests per minute
        da_fee_per_byte: Fee per byte for DA posting
        rpc_fee_flat: Flat fee per RPC call
        aicf_rate_per_unit: Cost per AICF resource unit
        aicf_free_units: Free AICF units per period
    """
    
    name: str
    rate_limit_rpm: int
    da_fee_per_byte: float
    rpc_fee_flat: float
    aicf_rate_per_unit: float
    aicf_free_units: int


@dataclass(frozen=True)
class DAFeeConfig:
    """
    DA (Data Availability) fee configuration.
    
    Attributes:
        fee_per_byte: Fee per byte posted to DA layer
        treasury_address: Address to receive treasury portion of fees
        validator_split: Fraction of fees to validators (0-1), rest to treasury
    """
    
    fee_per_byte: float
    treasury_address: str
    validator_split: float
    
    def validate(self) -> None:
        """Validate configuration."""
        if self.fee_per_byte < 0:
            raise ValueError("fee_per_byte must be non-negative")
        if not 0 <= self.validator_split <= 1:
            raise ValueError("validator_split must be between 0 and 1")


@dataclass(frozen=True)
class RPCFeeConfig:
    """
    RPC fee configuration.
    
    Attributes:
        fee_flat: Flat fee per RPC call
        treasury_address: Address to receive treasury portion of fees
        validator_split: Fraction of fees to validators (0-1), rest to treasury
    """
    
    fee_flat: float
    treasury_address: str
    validator_split: float
    
    def validate(self) -> None:
        """Validate configuration."""
        if self.fee_flat < 0:
            raise ValueError("fee_flat must be non-negative")
        if not 0 <= self.validator_split <= 1:
            raise ValueError("validator_split must be between 0 and 1")


@dataclass(frozen=True)
class AICFBillingConfig:
    """
    AICF (AI Compute Fund) billing configuration.
    
    Attributes:
        mode: Billing mode ("free" or "paid")
        rate_per_unit: Cost per AICF resource unit
        free_units: Free tier units per period
    """
    
    mode: Literal["free", "paid"]
    rate_per_unit: float
    free_units: int
    
    def validate(self) -> None:
        """Validate configuration."""
        if self.rate_per_unit < 0:
            raise ValueError("rate_per_unit must be non-negative")
        if self.free_units < 0:
            raise ValueError("free_units must be non-negative")


@dataclass(frozen=True)
class BillingConfig:
    """
    Top-level billing configuration.
    
    Attributes:
        mode: Billing mode ("free" or "paid")
        api_key_header: HTTP header name for API key
        default_plan: Default plan name for new/unknown users
        plans: Available billing plans
        da_fee: DA fee configuration
        rpc_fee: RPC fee configuration
        aicf: AICF billing configuration
    """
    
    mode: Literal["free", "paid"]
    api_key_header: str
    default_plan: str
    plans: Dict[str, PlanConfig]
    da_fee: DAFeeConfig
    rpc_fee: RPCFeeConfig
    aicf: AICFBillingConfig
    
    def get_plan(self, plan_name: str) -> PlanConfig:
        """Get plan configuration by name, falls back to default plan."""
        return self.plans.get(plan_name, self.plans[self.default_plan])
    
    def validate(self) -> None:
        """Validate configuration."""
        if self.default_plan not in self.plans:
            raise ValueError(f"default_plan '{self.default_plan}' not in plans")
        if self.mode not in ("free", "paid"):
            raise ValueError("mode must be 'free' or 'paid'")
        self.da_fee.validate()
        self.rpc_fee.validate()
        self.aicf.validate()


def load_billing_config() -> BillingConfig:
    """
    Load billing configuration from environment variables.
    
    Returns:
        BillingConfig with values from environment or defaults
        
    Environment Variables:
        ANIMICA_BILLING_MODE: "free" (default) or "paid"
        ANIMICA_API_KEY_HEADER: Header name (default "x-animica-key")
        ANIMICA_DEFAULT_PLAN: Default plan name (default "free")
        ANIMICA_RATE_LIMIT_FREE: Free tier RPM (default 60)
        ANIMICA_RATE_LIMIT_PRO: Pro tier RPM (default 600)
        ANIMICA_DA_FEE_PER_BYTE: DA fee per byte (default 0)
        ANIMICA_RPC_FEE_FLAT: RPC flat fee (default 0)
        ANIMICA_FEE_TREASURY_ADDRESS: Treasury address (default "")
        ANIMICA_FEE_VALIDATOR_SPLIT: Validator split 0-1 (default 1.0)
        ANIMICA_AICF_BILLING_MODE: AICF mode (default "free")
        ANIMICA_AICF_RATE_PER_UNIT: AICF unit rate (default 0)
        ANIMICA_AICF_FREE_UNITS: AICF free units (default 1000)
    """
    
    # Billing mode
    mode: Literal["free", "paid"] = "paid" if _getenv_bool("ANIMICA_BILLING_MODE", False) else "free"
    
    # API key header
    api_key_header = _getenv("ANIMICA_API_KEY_HEADER", "x-animica-key")
    
    # Default plan
    default_plan = _getenv("ANIMICA_DEFAULT_PLAN", "free")
    
    # Rate limits
    rate_limit_free = _getenv_int("ANIMICA_RATE_LIMIT_FREE", 60)
    rate_limit_pro = _getenv_int("ANIMICA_RATE_LIMIT_PRO", 600)
    rate_limit_enterprise = _getenv_int("ANIMICA_RATE_LIMIT_ENTERPRISE", 6000)
    
    # DA fees
    da_fee_per_byte = _getenv_float("ANIMICA_DA_FEE_PER_BYTE", 0.0)
    
    # RPC fees
    rpc_fee_flat = _getenv_float("ANIMICA_RPC_FEE_FLAT", 0.0)
    
    # Treasury and validator split
    treasury_address = _getenv("ANIMICA_FEE_TREASURY_ADDRESS", "")
    validator_split = _getenv_float("ANIMICA_FEE_VALIDATOR_SPLIT", 1.0)
    
    # AICF billing
    aicf_mode: Literal["free", "paid"] = "paid" if _getenv_bool("ANIMICA_AICF_BILLING_MODE", False) else "free"
    aicf_rate_per_unit = _getenv_float("ANIMICA_AICF_RATE_PER_UNIT", 0.0)
    aicf_free_units = _getenv_int("ANIMICA_AICF_FREE_UNITS", 1000)
    
    # Build plan configurations
    plans = {
        "free": PlanConfig(
            name="free",
            rate_limit_rpm=rate_limit_free,
            da_fee_per_byte=da_fee_per_byte,
            rpc_fee_flat=rpc_fee_flat,
            aicf_rate_per_unit=aicf_rate_per_unit,
            aicf_free_units=aicf_free_units,
        ),
        "pro": PlanConfig(
            name="pro",
            rate_limit_rpm=rate_limit_pro,
            da_fee_per_byte=da_fee_per_byte,
            rpc_fee_flat=rpc_fee_flat,
            aicf_rate_per_unit=aicf_rate_per_unit,
            aicf_free_units=aicf_free_units * 10,  # 10x free units for pro
        ),
        "enterprise": PlanConfig(
            name="enterprise",
            rate_limit_rpm=rate_limit_enterprise,
            da_fee_per_byte=da_fee_per_byte * 0.5,  # 50% discount for enterprise
            rpc_fee_flat=rpc_fee_flat * 0.5,
            aicf_rate_per_unit=aicf_rate_per_unit * 0.5,
            aicf_free_units=aicf_free_units * 100,  # 100x free units for enterprise
        ),
    }
    
    config = BillingConfig(
        mode=mode,
        api_key_header=api_key_header,
        default_plan=default_plan,
        plans=plans,
        da_fee=DAFeeConfig(
            fee_per_byte=da_fee_per_byte,
            treasury_address=treasury_address,
            validator_split=validator_split,
        ),
        rpc_fee=RPCFeeConfig(
            fee_flat=rpc_fee_flat,
            treasury_address=treasury_address,
            validator_split=validator_split,
        ),
        aicf=AICFBillingConfig(
            mode=aicf_mode,
            rate_per_unit=aicf_rate_per_unit,
            free_units=aicf_free_units,
        ),
    )
    
    config.validate()
    return config


# Singleton instance for convenience
_config: Optional[BillingConfig] = None


def get_billing_config() -> BillingConfig:
    """Get or create singleton billing configuration."""
    global _config
    if _config is None:
        _config = load_billing_config()
    return _config


__all__ = [
    "PlanConfig",
    "DAFeeConfig",
    "RPCFeeConfig",
    "AICFBillingConfig",
    "BillingConfig",
    "load_billing_config",
    "get_billing_config",
]
