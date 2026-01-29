"""
Animica Billing Module
======================

Shared billing configuration and utilities for monetization across the platform.

This module provides:
- Environment-driven billing configuration (free/paid modes)
- API key management and plan lookup
- Rate limiting configuration per plan
- Fee calculation for DA, RPC, and AICF services
- Treasury/validator split logic
- Usage tracking stores (in-memory and file-backed)

Default Mode: Free/Devnet (all fees = 0, no authentication required)
Paid Mode: Enabled via ANIMICA_BILLING_MODE=paid

Environment Variables:
- ANIMICA_BILLING_MODE: "free" (default) or "paid"
- ANIMICA_API_KEY_HEADER: Header name for API keys (default "x-animica-key")
- ANIMICA_DEFAULT_PLAN: Default plan for new users (default "free")
- ANIMICA_RATE_LIMIT_FREE: Requests per minute for free tier (default 60)
- ANIMICA_RATE_LIMIT_PRO: Requests per minute for pro tier (default 600)
- ANIMICA_DA_FEE_PER_BYTE: Fee per byte for DA posting (default 0)
- ANIMICA_RPC_FEE_FLAT: Flat fee per RPC call (default 0)
- ANIMICA_FEE_TREASURY_ADDRESS: Treasury address for fee collection
- ANIMICA_FEE_VALIDATOR_SPLIT: Fraction to validators 0-1 (default 1.0, all to validators)
- ANIMICA_AICF_BILLING_MODE: "free" (default) or "paid"
- ANIMICA_AICF_RATE_PER_UNIT: Cost per AICF resource unit (default 0)
- ANIMICA_AICF_FREE_UNITS: Free tier units per period (default 1000)
"""

from billing.config import (
    AICFBillingConfig,
    BillingConfig,
    DAFeeConfig,
    PlanConfig,
    RPCFeeConfig,
    load_billing_config,
)
from billing.store import FileBackedUsageStore, UsageStore
from billing.utils import compute_da_fee, compute_fee_split, validate_api_key

__version__ = "0.1.0"

__all__ = [
    "BillingConfig",
    "PlanConfig",
    "DAFeeConfig",
    "RPCFeeConfig",
    "AICFBillingConfig",
    "load_billing_config",
    "UsageStore",
    "FileBackedUsageStore",
    "compute_da_fee",
    "compute_fee_split",
    "validate_api_key",
]
