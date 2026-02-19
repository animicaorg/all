"""
AICF Economic Routing Configuration
===================================

Configuration for fee routing and economic parameters.

This module defines the routing of funds from various sources to AICF:
- Block rewards
- Transaction fees
- ENA call fees
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EconomicRoutingConfig:
    """
    Configuration for AICF economic routing.
    
    All percentages are in basis points (1 bp = 0.01%).
    """
    
    # Block reward routing (sum should be <= 10000 bps)
    block_reward_aicf_bps: int = 1000  # 10% to AICF
    block_reward_miner_bps: int = 9000  # 90% to miner
    block_reward_treasury_bps: int = 0  # 0% to treasury
    
    # Transaction fee routing (sum should be <= 10000 bps)
    tx_fee_aicf_bps: int = 2000  # 20% to AICF
    tx_fee_operator_bps: int = 7000  # 70% to operator/miner
    tx_fee_burn_bps: int = 1000  # 10% burn
    
    # ENA call fee routing (sum should be <= 10000 bps)
    ena_fee_aicf_bps: int = 7000  # 70% to AICF
    ena_fee_operator_bps: int = 2000  # 20% to service operator
    ena_fee_burn_bps: int = 1000  # 10% burn or reserve
    
    def validate(self) -> tuple[bool, Optional[str]]:
        """
        Validate configuration.
        
        Returns:
            (is_valid, error_message) tuple
        """
        # Check block reward sum
        block_total = (
            self.block_reward_aicf_bps +
            self.block_reward_miner_bps +
            self.block_reward_treasury_bps
        )
        if block_total > 10_000:
            return False, f"Block reward routing exceeds 100%: {block_total} bps"
        
        # Check tx fee sum
        tx_total = (
            self.tx_fee_aicf_bps +
            self.tx_fee_operator_bps +
            self.tx_fee_burn_bps
        )
        if tx_total > 10_000:
            return False, f"Tx fee routing exceeds 100%: {tx_total} bps"
        
        # Check ENA fee sum
        ena_total = (
            self.ena_fee_aicf_bps +
            self.ena_fee_operator_bps +
            self.ena_fee_burn_bps
        )
        if ena_total > 10_000:
            return False, f"ENA fee routing exceeds 100%: {ena_total} bps"
        
        # Check all values are non-negative
        for field_name in [
            'block_reward_aicf_bps', 'block_reward_miner_bps', 'block_reward_treasury_bps',
            'tx_fee_aicf_bps', 'tx_fee_operator_bps', 'tx_fee_burn_bps',
            'ena_fee_aicf_bps', 'ena_fee_operator_bps', 'ena_fee_burn_bps',
        ]:
            value = getattr(self, field_name)
            if value < 0:
                return False, f"{field_name} cannot be negative: {value}"
        
        return True, None


# Default configuration
DEFAULT_CONFIG = EconomicRoutingConfig()


def load_config_from_params(params: dict) -> EconomicRoutingConfig:
    """
    Load configuration from params.yaml structure.
    
    Args:
        params: Params dict (from spec/params.yaml)
        
    Returns:
        EconomicRoutingConfig instance
    """
    aicf_params = params.get("aicf", {})
    ena_params = params.get("ena", {})
    
    config = EconomicRoutingConfig(
        # Block rewards
        block_reward_aicf_bps=aicf_params.get("block_reward_slice_bps", 1000),
        # Tx fees
        tx_fee_aicf_bps=aicf_params.get("fee_slice_bps", 2000),
        # ENA fees
        ena_fee_aicf_bps=ena_params.get("call_fee_aicf_bps", 7000),
        ena_fee_operator_bps=ena_params.get("call_fee_operator_bps", 2000),
        ena_fee_burn_bps=ena_params.get("call_fee_burn_bps", 1000),
    )
    
    # Validate
    is_valid, error = config.validate()
    if not is_valid:
        raise ValueError(f"Invalid economic routing config: {error}")
    
    return config


def compute_block_reward_split(
    total_reward: int,
    config: Optional[EconomicRoutingConfig] = None,
) -> tuple[int, int, int]:
    """
    Split block reward according to routing configuration.
    
    Args:
        total_reward: Total block reward (in base units)
        config: Economic routing config (uses default if None)
        
    Returns:
        (miner_amount, aicf_amount, treasury_amount) tuple
    """
    if config is None:
        config = DEFAULT_CONFIG
    
    # Calculate splits (truncate to integer)
    aicf_amount = (total_reward * config.block_reward_aicf_bps) // 10_000
    treasury_amount = (total_reward * config.block_reward_treasury_bps) // 10_000
    
    # Miner gets remainder to ensure total is preserved
    miner_amount = total_reward - aicf_amount - treasury_amount
    
    return miner_amount, aicf_amount, treasury_amount


def compute_tx_fee_split(
    total_fee: int,
    config: Optional[EconomicRoutingConfig] = None,
) -> tuple[int, int, int]:
    """
    Split transaction fee according to routing configuration.
    
    Args:
        total_fee: Total transaction fee (in base units)
        config: Economic routing config (uses default if None)
        
    Returns:
        (operator_amount, aicf_amount, burn_amount) tuple
    """
    if config is None:
        config = DEFAULT_CONFIG
    
    # Calculate splits
    aicf_amount = (total_fee * config.tx_fee_aicf_bps) // 10_000
    burn_amount = (total_fee * config.tx_fee_burn_bps) // 10_000
    
    # Operator gets remainder
    operator_amount = total_fee - aicf_amount - burn_amount
    
    return operator_amount, aicf_amount, burn_amount


def compute_ena_fee_split(
    total_fee: int,
    config: Optional[EconomicRoutingConfig] = None,
) -> tuple[int, int, int]:
    """
    Split ENA call fee according to routing configuration.
    
    Args:
        total_fee: Total ENA call fee (in base units)
        config: Economic routing config (uses default if None)
        
    Returns:
        (aicf_amount, operator_amount, reserve_amount) tuple
    """
    if config is None:
        config = DEFAULT_CONFIG
    
    # Calculate splits
    aicf_amount = (total_fee * config.ena_fee_aicf_bps) // 10_000
    operator_amount = (total_fee * config.ena_fee_operator_bps) // 10_000
    
    # Reserve/burn gets remainder
    reserve_amount = total_fee - aicf_amount - operator_amount
    
    return aicf_amount, operator_amount, reserve_amount


__all__ = [
    "EconomicRoutingConfig",
    "DEFAULT_CONFIG",
    "load_config_from_params",
    "compute_block_reward_split",
    "compute_tx_fee_split",
    "compute_ena_fee_split",
]
