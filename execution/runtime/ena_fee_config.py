"""
execution.runtime.ena_fee_config — ENA Call Fee Configuration
==============================================================

Defines fee split parameters for ENA inference calls.
Fee routing distributes payment among:
- AICF pool (for ecosystem funding)
- Provider (for compute work)
- Optional: Treasury, Burn

All splits are in basis points (1 bp = 0.01%, 10000 bp = 100%).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ENAFeeConfig:
    """
    Fee split configuration for ENA calls.
    
    All values are in basis points (bp), where 10000 bp = 100%.
    The sum of all splits must equal 10000.
    """
    
    aicf_bp: int = 6000  # 60% to AICF pool
    provider_bp: int = 3500  # 35% to provider
    burn_bp: int = 0  # 0% burned
    treasury_bp: int = 500  # 5% to treasury
    
    # Minimum fee in nano-ANM
    min_fee_nano: int = 10_000  # 0.00001 ANM minimum
    
    def __post_init__(self):
        """Validate fee configuration."""
        total = self.aicf_bp + self.provider_bp + self.burn_bp + self.treasury_bp
        if total != 10_000:
            raise ValueError(
                f"Fee splits must sum to 10000 bp, got {total} "
                f"(aicf={self.aicf_bp}, provider={self.provider_bp}, "
                f"burn={self.burn_bp}, treasury={self.treasury_bp})"
            )
        
        if self.aicf_bp < 0 or self.provider_bp < 0 or self.burn_bp < 0 or self.treasury_bp < 0:
            raise ValueError("All fee split values must be non-negative")
        
        if self.min_fee_nano < 0:
            raise ValueError("min_fee_nano must be non-negative")
    
    def split_fee(self, total_fee: int) -> dict[str, int]:
        """
        Split a total fee into component parts.
        
        Args:
            total_fee: Total fee in nano-ANM
        
        Returns:
            Dictionary with keys: aicf_cut, provider_cut, burn_cut, treasury_cut
        """
        if total_fee < 0:
            raise ValueError("total_fee must be non-negative")
        
        # Compute each split (integer division, truncation toward zero)
        aicf_cut = (total_fee * self.aicf_bp) // 10_000
        provider_cut = (total_fee * self.provider_bp) // 10_000
        burn_cut = (total_fee * self.burn_bp) // 10_000
        treasury_cut = (total_fee * self.treasury_bp) // 10_000
        
        # Handle rounding: distribute remainder to AICF (largest beneficiary)
        distributed = aicf_cut + provider_cut + burn_cut + treasury_cut
        remainder = total_fee - distributed
        if remainder > 0:
            aicf_cut += remainder
        
        return {
            "aicf_cut": aicf_cut,
            "provider_cut": provider_cut,
            "burn_cut": burn_cut,
            "treasury_cut": treasury_cut,
        }
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "aicf_bp": self.aicf_bp,
            "provider_bp": self.provider_bp,
            "burn_bp": self.burn_bp,
            "treasury_bp": self.treasury_bp,
            "min_fee_nano": str(self.min_fee_nano),  # String to avoid BigInt issues
        }


def get_default_ena_fee_config() -> ENAFeeConfig:
    """Get default ENA fee configuration."""
    return ENAFeeConfig(
        aicf_bp=6000,  # 60%
        provider_bp=3500,  # 35%
        burn_bp=0,  # 0%
        treasury_bp=500,  # 5%
        min_fee_nano=10_000,  # 0.00001 ANM
    )


def load_ena_fee_config_from_params(params: Optional[dict]) -> ENAFeeConfig:
    """
    Load ENA fee configuration from chain parameters.
    
    Args:
        params: Chain parameters dictionary
    
    Returns:
        ENAFeeConfig instance
    """
    if params is None:
        return get_default_ena_fee_config()
    
    ena_params = params.get("ena", {})
    fee_params = ena_params.get("fee_config", {})
    
    return ENAFeeConfig(
        aicf_bp=fee_params.get("aicf_bp", 6000),
        provider_bp=fee_params.get("provider_bp", 3500),
        burn_bp=fee_params.get("burn_bp", 0),
        treasury_bp=fee_params.get("treasury_bp", 500),
        min_fee_nano=fee_params.get("min_fee_nano", 10_000),
    )


__all__ = [
    "ENAFeeConfig",
    "get_default_ena_fee_config",
    "load_ena_fee_config_from_params",
]
