"""
Billing utility functions.

Helper functions for fee calculation, validation, and common billing operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class FeeSplit:
    """
    Fee split between treasury and validators.
    
    Attributes:
        total_fee: Total fee amount
        validator_amount: Amount going to validators
        treasury_amount: Amount going to treasury
        validator_split: Fraction to validators (0-1)
    """
    
    total_fee: float
    validator_amount: float
    treasury_amount: float
    validator_split: float


def compute_da_fee(bytes_count: int, fee_per_byte: float) -> float:
    """
    Compute DA fee for a given number of bytes.
    
    Args:
        bytes_count: Number of bytes posted
        fee_per_byte: Fee per byte
        
    Returns:
        Total DA fee
    """
    if bytes_count < 0:
        raise ValueError("bytes_count must be non-negative")
    if fee_per_byte < 0:
        raise ValueError("fee_per_byte must be non-negative")
    
    return bytes_count * fee_per_byte


def compute_fee_split(total_fee: float, validator_split: float) -> FeeSplit:
    """
    Compute fee split between validators and treasury.
    
    Args:
        total_fee: Total fee amount
        validator_split: Fraction to validators (0-1)
        
    Returns:
        FeeSplit with validator and treasury amounts
    """
    if total_fee < 0:
        raise ValueError("total_fee must be non-negative")
    if not 0 <= validator_split <= 1:
        raise ValueError("validator_split must be between 0 and 1")
    
    validator_amount = total_fee * validator_split
    treasury_amount = total_fee * (1 - validator_split)
    
    return FeeSplit(
        total_fee=total_fee,
        validator_amount=validator_amount,
        treasury_amount=treasury_amount,
        validator_split=validator_split,
    )


def validate_api_key(api_key: str, valid_keys: Optional[Dict[str, str]] = None) -> Tuple[bool, Optional[str]]:
    """
    Validate API key and return plan.
    
    Args:
        api_key: The API key to validate
        valid_keys: Optional dictionary mapping API keys to plan names
        
    Returns:
        Tuple of (is_valid, plan_name)
        
    Note:
        If valid_keys is None, any non-empty key is considered valid with "free" plan.
        This allows for free/devnet mode where authentication is not enforced.
    """
    if not api_key:
        return (False, None)
    
    # If no validation dict provided, accept any key with free plan (devnet mode)
    if valid_keys is None:
        return (True, "free")
    
    # Check if key exists in validation dict
    plan = valid_keys.get(api_key)
    if plan is None:
        return (False, None)
    
    return (True, plan)


def compute_aicf_cost(
    units: int,
    rate_per_unit: float,
    free_units: int,
    units_used: int,
) -> Tuple[float, int]:
    """
    Compute AICF job cost considering free tier.
    
    Args:
        units: Number of units requested
        rate_per_unit: Cost per unit
        free_units: Free tier units available per period
        units_used: Units already used in current period
        
    Returns:
        Tuple of (cost, billable_units)
    """
    if units < 0:
        raise ValueError("units must be non-negative")
    if rate_per_unit < 0:
        raise ValueError("rate_per_unit must be non-negative")
    if free_units < 0:
        raise ValueError("free_units must be non-negative")
    if units_used < 0:
        raise ValueError("units_used must be non-negative")
    
    # Calculate remaining free units
    remaining_free = max(0, free_units - units_used)
    
    # Calculate billable units
    billable_units = max(0, units - remaining_free)
    
    # Calculate cost
    cost = billable_units * rate_per_unit
    
    return (cost, billable_units)


__all__ = [
    "FeeSplit",
    "compute_da_fee",
    "compute_fee_split",
    "validate_api_key",
    "compute_aicf_cost",
]
