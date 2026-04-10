"""
aicf.aitypes.payout_accounting - Payout Accounting & Maturity (Phase 2)
========================================================================

Deterministic payout accounting with reorg-safe maturity depth.
Tracks provider reward accruals and prevents double-claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional
from datetime import datetime, timezone

__all__ = [
    "MaturityConfig",
    "ProviderAccrual",
    "PayoutEpoch",
    "ClaimRecord",
]


@dataclass(frozen=True)
class MaturityConfig:
    """
    Configuration for receipt maturity and finalization.
    Prevents payout of receipts from blocks that may be reorg'd.
    """
    # Maturity depth (blocks) - receipts only count after this many confirmations
    maturity_depth_blocks: int = 50
    
    # Epoch configuration
    epoch_length_blocks: int = 100  # Payout epoch duration
    
    # Reserve buffer (% of pool balance kept in reserve)
    reserve_ratio_bps: int = 1000  # 10%
    
    # Pull vs push payout mode
    payout_mode: str = "pull"  # "pull" or "push"
    
    # Claim limits
    min_claim_amount: int = 1_000_000  # 0.000001 ANM minimum claim
    max_claims_per_epoch: int = 1000  # DoS prevention
    
    def __post_init__(self):
        if self.maturity_depth_blocks < 0:
            raise ValueError("maturity_depth_blocks must be >= 0")
        if self.epoch_length_blocks <= 0:
            raise ValueError("epoch_length_blocks must be > 0")
        if self.reserve_ratio_bps < 0 or self.reserve_ratio_bps > 10000:
            raise ValueError("reserve_ratio_bps must be in [0, 10000]")
        if self.payout_mode not in ("pull", "push"):
            raise ValueError(f"payout_mode must be 'pull' or 'push', got {self.payout_mode}")
        if self.min_claim_amount < 0:
            raise ValueError("min_claim_amount must be >= 0")
        if self.max_claims_per_epoch <= 0:
            raise ValueError("max_claims_per_epoch must be > 0")


@dataclass(frozen=False)
class ProviderAccrual:
    """
    Per-provider reward accrual tracking.
    
    Mutable to allow incremental updates as receipts mature.
    Keyed by (provider_id, epoch).
    """
    provider_id: str
    epoch: int
    
    # Accrued rewards (in nano-ANM)
    accrued_total: int = 0  # Total accrued this epoch
    claimed_total: int = 0  # Total claimed so far
    
    # Receipt tracking
    receipt_count: int = 0  # Number of receipts contributing to accrual
    tokens_processed: int = 0  # Total tokens processed this epoch
    
    # Finalization status
    is_finalized: bool = False  # True when epoch ends and receipts mature
    finalized_at_height: Optional[int] = None  # Block height when finalized
    
    def claimable(self) -> int:
        """Amount available to claim (accrued - claimed)"""
        return max(0, self.accrued_total - self.claimed_total)
    
    def can_claim(self, amount: int, config: MaturityConfig) -> bool:
        """Check if a claim amount is valid"""
        if not self.is_finalized:
            return False
        if amount <= 0:
            return False
        if amount < config.min_claim_amount:
            return False
        if amount > self.claimable():
            return False
        return True
    
    def record_claim(self, amount: int):
        """Record a successful claim"""
        if amount > self.claimable():
            raise ValueError(f"Cannot claim {amount}, only {self.claimable()} available")
        self.claimed_total += amount
    
    def add_receipt(self, provider_cut: int, tokens: int):
        """Add a matured receipt to accrual"""
        if self.is_finalized:
            raise ValueError("Cannot add receipts to finalized epoch")
        self.accrued_total += provider_cut
        self.receipt_count += 1
        self.tokens_processed += tokens
    
    def finalize(self, height: int):
        """Mark epoch as finalized"""
        self.is_finalized = True
        self.finalized_at_height = height


@dataclass(frozen=False)
class PayoutEpoch:
    """
    Per-epoch payout accounting.
    Tracks total pool inflows, distributions, and reserves.
    """
    epoch: int
    start_height: int
    end_height: int
    
    # Pool accounting
    inflow_total: int = 0  # Total AICF pool inflow this epoch
    distributed_total: int = 0  # Total distributed to providers
    reserve_held: int = 0  # Amount held in reserve
    
    # Provider accruals (mapping: provider_id -> accrued_amount)
    provider_accruals: Dict[str, int] = field(default_factory=dict)
    
    # Finalization
    is_finalized: bool = False
    finalized_at_height: Optional[int] = None
    finalized_at: Optional[datetime] = None
    
    def total_allocated(self) -> int:
        """Total allocated to providers"""
        return sum(self.provider_accruals.values())
    
    def can_distribute(self, amount: int, reserve_ratio_bps: int) -> bool:
        """Check if amount can be distributed without violating reserve"""
        reserve_required = (self.inflow_total * reserve_ratio_bps) // 10000
        available = self.inflow_total - reserve_required - self.distributed_total
        return amount <= available
    
    def allocate(self, provider_id: str, amount: int):
        """Allocate rewards to a provider"""
        if self.is_finalized:
            raise ValueError("Cannot allocate to finalized epoch")
        current = self.provider_accruals.get(provider_id, 0)
        self.provider_accruals[provider_id] = current + amount
    
    def distribute(self, amount: int):
        """Record a distribution"""
        self.distributed_total += amount
    
    def finalize(self, height: int, reserve_ratio_bps: int):
        """Finalize epoch and compute reserves"""
        reserve_required = (self.inflow_total * reserve_ratio_bps) // 10000
        self.reserve_held = reserve_required
        self.is_finalized = True
        self.finalized_at_height = height
        self.finalized_at = datetime.now(timezone.utc)


@dataclass(frozen=True)
class ClaimRecord:
    """
    Immutable record of a successful provider claim.
    Used for audit trail and double-claim prevention.
    """
    claim_id: bytes  # 32-byte unique claim identifier
    provider_id: str
    epoch: int
    amount: int  # Amount claimed (in nano-ANM)
    to_address: bytes  # 32-byte destination address
    claimed_at_height: int
    claimed_at: datetime
    tx_hash: bytes  # 32-byte transaction hash that executed the claim
    
    def __post_init__(self):
        if len(self.claim_id) != 32:
            raise ValueError("claim_id must be 32 bytes")
        if not self.provider_id:
            raise ValueError("provider_id cannot be empty")
        if self.epoch < 0:
            raise ValueError("epoch must be >= 0")
        if self.amount <= 0:
            raise ValueError("amount must be > 0")
        if len(self.to_address) != 32:
            raise ValueError("to_address must be 32 bytes")
        if self.claimed_at_height < 0:
            raise ValueError("claimed_at_height must be >= 0")
        if len(self.tx_hash) != 32:
            raise ValueError("tx_hash must be 32 bytes")
