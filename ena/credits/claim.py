"""
Credit claim management.

Handles partial and full credit claiming for workers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

__all__ = ["ClaimResult", "ClaimManager"]


@dataclass
class ClaimResult:
    """
    Result of a credit claim operation.
    
    Attributes:
        worker_id: Worker making the claim
        amount_claimed: Amount of credits claimed
        remaining_balance: Remaining credits after claim
        success: Whether claim succeeded
        message: Explanation of result
    """
    worker_id: str
    amount_claimed: int
    remaining_balance: int
    success: bool
    message: str


class ClaimManager:
    """
    Manager for credit claims.
    
    Tracks worker balances and processes claims.
    """
    
    def __init__(self):
        """Initialize claim manager."""
        self.balances: Dict[str, int] = {}
    
    def add_credits(self, worker_id: str, amount: int):
        """
        Add credits to a worker's balance.
        
        Args:
            worker_id: Worker to credit
            amount: Amount to add
        """
        if worker_id not in self.balances:
            self.balances[worker_id] = 0
        
        self.balances[worker_id] += amount
    
    def get_balance(self, worker_id: str) -> int:
        """
        Get worker's current balance.
        
        Args:
            worker_id: Worker to query
        
        Returns:
            Current balance
        """
        return self.balances.get(worker_id, 0)
    
    def claim(
        self,
        worker_id: str,
        amount: Optional[int] = None,
    ) -> ClaimResult:
        """
        Claim credits (partial or full).
        
        Args:
            worker_id: Worker making claim
            amount: Amount to claim (None = full balance)
        
        Returns:
            ClaimResult with details
        """
        current_balance = self.get_balance(worker_id)
        
        if amount is None:
            # Full claim
            amount = current_balance
        
        if amount <= 0:
            return ClaimResult(
                worker_id=worker_id,
                amount_claimed=0,
                remaining_balance=current_balance,
                success=False,
                message="Invalid claim amount (must be > 0)",
            )
        
        if amount > current_balance:
            return ClaimResult(
                worker_id=worker_id,
                amount_claimed=0,
                remaining_balance=current_balance,
                success=False,
                message=f"Insufficient balance ({current_balance} < {amount})",
            )
        
        # Process claim
        self.balances[worker_id] = current_balance - amount
        
        return ClaimResult(
            worker_id=worker_id,
            amount_claimed=amount,
            remaining_balance=current_balance - amount,
            success=True,
            message=f"Claimed {amount} credits successfully",
        )
