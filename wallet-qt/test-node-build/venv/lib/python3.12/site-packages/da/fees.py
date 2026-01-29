"""
DA Fee Accounting Module.

Computes and tracks fees for Data Availability posting operations.
Integrates with billing module for fee calculation and treasury/validator splits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

# Import billing utilities
try:
    from billing.config import get_billing_config
    from billing.utils import compute_da_fee, compute_fee_split, FeeSplit
    
    BILLING_AVAILABLE = True
except ImportError:
    BILLING_AVAILABLE = False


@dataclass
class DAFeeReceipt:
    """
    Receipt for DA fee payment.
    
    Attributes:
        bytes_posted: Number of bytes posted to DA
        total_fee: Total fee charged
        validator_amount: Amount going to validators
        treasury_amount: Amount going to treasury
        fee_per_byte: Rate used for fee calculation
        treasury_address: Treasury address for fee collection
        validator_split: Fraction of fee to validators (0-1)
    """
    
    bytes_posted: int
    total_fee: float
    validator_amount: float
    treasury_amount: float
    fee_per_byte: float
    treasury_address: str
    validator_split: float


def calculate_da_fee(bytes_count: int, api_key: Optional[str] = None) -> DAFeeReceipt:
    """
    Calculate DA fee for a blob posting.
    
    Args:
        bytes_count: Number of bytes being posted
        api_key: Optional API key for plan-based pricing (future use)
        
    Returns:
        DAFeeReceipt with fee breakdown
        
    Raises:
        ValueError: If bytes_count is negative
        RuntimeError: If billing module not available
    """
    if not BILLING_AVAILABLE:
        # Fallback to zero fees if billing not available
        return DAFeeReceipt(
            bytes_posted=bytes_count,
            total_fee=0.0,
            validator_amount=0.0,
            treasury_amount=0.0,
            fee_per_byte=0.0,
            treasury_address="",
            validator_split=1.0,
        )
    
    if bytes_count < 0:
        raise ValueError("bytes_count must be non-negative")
    
    # Get billing config
    config = get_billing_config()
    
    # Get fee configuration (use default plan for now)
    # In the future, could look up plan by api_key
    plan = config.get_plan(config.default_plan)
    fee_per_byte = plan.da_fee_per_byte
    
    # Compute total fee
    total_fee = compute_da_fee(bytes_count, fee_per_byte)
    
    # Compute split
    split = compute_fee_split(total_fee, config.da_fee.validator_split)
    
    return DAFeeReceipt(
        bytes_posted=bytes_count,
        total_fee=total_fee,
        validator_amount=split.validator_amount,
        treasury_amount=split.treasury_amount,
        fee_per_byte=fee_per_byte,
        treasury_address=config.da_fee.treasury_address,
        validator_split=config.da_fee.validator_split,
    )


def format_fee_receipt(receipt: DAFeeReceipt) -> Dict[str, object]:
    """
    Format fee receipt as dictionary for API responses.
    
    Args:
        receipt: DAFeeReceipt to format
        
    Returns:
        Dictionary representation of receipt
    """
    return {
        "bytes_posted": receipt.bytes_posted,
        "total_fee": receipt.total_fee,
        "validator_amount": receipt.validator_amount,
        "treasury_amount": receipt.treasury_amount,
        "fee_per_byte": receipt.fee_per_byte,
        "treasury_address": receipt.treasury_address,
        "validator_split": receipt.validator_split,
    }


__all__ = [
    "DAFeeReceipt",
    "calculate_da_fee",
    "format_fee_receipt",
]
