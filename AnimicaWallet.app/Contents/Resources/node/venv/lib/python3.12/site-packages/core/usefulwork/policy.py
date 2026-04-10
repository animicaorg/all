"""
Policy gating for Useful Work Proofs.

Controls which proof schemes are enabled, version requirements, caps, and bonuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Set, Optional
import yaml
from pathlib import Path


@dataclass
class SchemePolicy:
    """Policy for a single proof scheme."""
    enabled: bool = True
    min_version: str = "0.0.0"
    bonus_credits: int = 0  # Fixed bonus credits per accepted proof
    bonus_credits_bp: int = 0  # Basis points (10000 = 100%) of base reward
    per_share_cap: int = 1  # Max proofs of this scheme per share
    per_miner_hourly_cap: int = 100  # Max proofs from a miner per hour
    per_epoch_cap: int = 10000  # Global cap per epoch
    max_verify_ms: int = 500  # Max verification time in milliseconds


@dataclass
class UWPPolicy:
    """
    Global policy for Useful Work Proofs.
    
    Controls enabled schemes, caps, and bonus rewards.
    """
    enabled: bool = True
    max_proofs_per_share: int = 5
    max_total_bytes_per_share: int = 262144  # 256 KB
    max_verify_ms_per_share: int = 2000  # 2 seconds total
    require_proofs: bool = False  # If True, shares must have valid proofs
    
    schemes: Dict[str, SchemePolicy] = field(default_factory=dict)
    
    # Default bonus from AICF escrow budget
    default_bonus_credits: int = 1000
    epoch_bonus_budget: int = 1000000  # Total bonus budget per epoch
    
    def is_scheme_enabled(self, scheme_id: str) -> bool:
        """Check if a scheme is enabled."""
        if not self.enabled:
            return False
        scheme = self.schemes.get(scheme_id)
        if scheme is None:
            return False
        return scheme.enabled
    
    def get_scheme_policy(self, scheme_id: str) -> Optional[SchemePolicy]:
        """Get policy for a specific scheme."""
        return self.schemes.get(scheme_id)
    
    def get_bonus_credits(self, scheme_id: str) -> int:
        """Get bonus credits for a scheme."""
        scheme = self.schemes.get(scheme_id)
        if scheme is None:
            return 0
        return scheme.bonus_credits or self.default_bonus_credits


# Global policy instance
_POLICY: Optional[UWPPolicy] = None


def load_policy(policy_file: Optional[Path] = None) -> UWPPolicy:
    """
    Load UWP policy from YAML file.
    
    Args:
        policy_file: Path to policy YAML file (defaults to spec/uwp_policy.yaml)
        
    Returns:
        Loaded policy
    """
    global _POLICY
    
    if policy_file is None:
        # Default to spec/uwp_policy.yaml
        policy_file = Path(__file__).parent.parent.parent / "spec" / "uwp_policy.yaml"
    
    if not policy_file.exists():
        # Return permissive default for devnets
        _POLICY = UWPPolicy(
            enabled=True,
            schemes={
                "ena.eval.micro": SchemePolicy(
                    enabled=True,
                    bonus_credits=2000,
                    max_verify_ms=300,
                ),
                "compute.receipt.v1": SchemePolicy(
                    enabled=True,
                    bonus_credits=5000,
                    max_verify_ms=200,
                ),
            }
        )
        return _POLICY
    
    with open(policy_file, "r") as f:
        data = yaml.safe_load(f)
    
    # Parse policy
    uwp_data = data.get("uwp", {})
    
    schemes = {}
    for scheme_id, scheme_data in uwp_data.get("schemes", {}).items():
        schemes[scheme_id] = SchemePolicy(
            enabled=scheme_data.get("enabled", True),
            min_version=scheme_data.get("min_version", "0.0.0"),
            bonus_credits=scheme_data.get("bonus_credits", 0),
            bonus_credits_bp=scheme_data.get("bonus_credits_bp", 0),
            per_share_cap=scheme_data.get("per_share_cap", 1),
            per_miner_hourly_cap=scheme_data.get("per_miner_hourly_cap", 100),
            per_epoch_cap=scheme_data.get("per_epoch_cap", 10000),
            max_verify_ms=scheme_data.get("max_verify_ms", 500),
        )
    
    _POLICY = UWPPolicy(
        enabled=uwp_data.get("enabled", True),
        max_proofs_per_share=uwp_data.get("max_proofs_per_share", 5),
        max_total_bytes_per_share=uwp_data.get("max_total_bytes_per_share", 262144),
        max_verify_ms_per_share=uwp_data.get("max_verify_ms_per_share", 2000),
        require_proofs=uwp_data.get("require_proofs", False),
        schemes=schemes,
        default_bonus_credits=uwp_data.get("default_bonus_credits", 1000),
        epoch_bonus_budget=uwp_data.get("epoch_bonus_budget", 1000000),
    )
    
    return _POLICY


def get_policy() -> UWPPolicy:
    """Get the global policy instance (loads if not already loaded)."""
    global _POLICY
    if _POLICY is None:
        _POLICY = load_policy()
    return _POLICY


def is_scheme_enabled(scheme_id: str) -> bool:
    """Check if a scheme is enabled (convenience function)."""
    return get_policy().is_scheme_enabled(scheme_id)


__all__ = [
    "SchemePolicy",
    "UWPPolicy",
    "load_policy",
    "get_policy",
    "is_scheme_enabled",
]
