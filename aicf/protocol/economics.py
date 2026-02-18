"""
AICF Protocol Economics
========================

Epoch accounting, credit minting, and reward distribution.

Handles:
- Inflow tracking and attribution
- Reward split calculation (GPU workers, treasury, dev, burn)
- Credit minting for verified work
- Claim amount calculation
- Deterministic rounding for basis point splits
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Dict, Optional, Tuple

from aicf.protocol.state import ProtocolState, ProtocolEpoch


@dataclass
class RewardSplit:
    """Reward split configuration in basis points."""
    gpu_workers_bp: int = 7000  # 70%
    treasury_bp: int = 2000      # 20%
    dev_bp: int = 500            # 5%
    burn_bp: int = 500           # 5%

    def __post_init__(self):
        """Validate split totals to 10000 bp."""
        total = self.gpu_workers_bp + self.treasury_bp + self.dev_bp + self.burn_bp
        if total != 10000:
            raise ValueError(f"Reward split must total 10000 bp, got {total}")


class EpochAccountant:
    """
    Manages epoch accounting, credit issuance, and reward distribution.
    
    Uses deterministic arithmetic with proper rounding to ensure
    consensus across all nodes.
    """

    def __init__(self, state: ProtocolState):
        """
        Initialize epoch accountant.
        
        Args:
            state: Protocol state manager
        """
        self.state = state

    def get_reward_split(self) -> RewardSplit:
        """Get current reward split from parameters."""
        params = self.state.get_all_params()
        return RewardSplit(
            gpu_workers_bp=int(params.get("reward_split_gpu_workers_bp", "7000")),
            treasury_bp=int(params.get("reward_split_treasury_bp", "2000")),
            dev_bp=int(params.get("reward_split_dev_bp", "500")),
            burn_bp=int(params.get("reward_split_burn_bp", "500")),
        )

    def record_inflow(
        self,
        epoch_id: int,
        source: str,
        amount: str,
        tx_hash: Optional[str] = None,
        block_height: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        """
        Record an inflow to AICF protocol.
        
        Args:
            epoch_id: Epoch to credit
            source: Source identifier ('ena', 'other', etc.)
            amount: Amount as decimal string (u256)
            tx_hash: Optional transaction hash
            block_height: Optional block height
            metadata: Optional metadata dict
        
        Returns:
            Inflow ID
        """
        from uuid import uuid4
        
        inflow_id = f"inflow_{uuid4().hex[:16]}"
        
        # Record inflow
        from aicf.protocol.state import AICFInflow
        self.state.record_inflow(AICFInflow(
            inflow_id=inflow_id,
            source=source,
            amount=amount,
            tx_hash=tx_hash,
            block_height=block_height,
            epoch_id=epoch_id,
            metadata=metadata,
        ))
        
        # Update epoch totals
        epoch = self.state.get_epoch(epoch_id)
        
        # Add to appropriate inflow counter
        if source == "ena":
            new_ena = str(int(epoch.inflow_ena) + int(amount))
            self.state.update_epoch(epoch_id, inflow_ena=new_ena)
        else:
            new_other = str(int(epoch.inflow_other) + int(amount))
            self.state.update_epoch(epoch_id, inflow_other=new_other)
        
        # Update total
        new_total = str(int(epoch.inflow_total) + int(amount))
        self.state.update_epoch(epoch_id, inflow_total=new_total)
        
        return inflow_id

    def award_credits(
        self,
        epoch_id: int,
        worker_id: str,
        credits: int,
    ) -> None:
        """
        Award credits to a worker for verified work.
        
        Args:
            epoch_id: Epoch ID
            worker_id: Worker ID
            credits: Credits to award
        """
        # Add credits to worker
        self.state.add_credits(epoch_id, worker_id, str(credits))
        
        # Update epoch total
        epoch = self.state.get_epoch(epoch_id)
        new_total = str(int(epoch.total_credits) + credits)
        self.state.update_epoch(epoch_id, total_credits=new_total)

    def finalize_epoch(
        self,
        epoch_id: int,
        end_height: int,
    ) -> Dict[str, str]:
        """
        Finalize an epoch and compute worker rewards.
        
        Args:
            epoch_id: Epoch to finalize
            end_height: Ending block height
        
        Returns:
            Dict mapping worker_id to reward amount (as string)
        """
        epoch = self.state.get_epoch(epoch_id)
        
        if epoch.finalized:
            raise ValueError(f"Epoch {epoch_id} already finalized")
        
        # Get reward split
        split = self.get_reward_split()
        
        # Calculate inflow for workers using basis points
        # inflow_for_workers = inflow_total * gpu_workers_bp / 10000
        # Use deterministic rounding: round down to avoid overpayment
        inflow_total = int(epoch.inflow_total)
        inflow_for_workers = (inflow_total * split.gpu_workers_bp) // 10000
        
        # Update epoch
        import time
        self.state.update_epoch(
            epoch_id,
            end_height=end_height,
            inflow_for_workers=str(inflow_for_workers),
            finalized=1,
            finalized_at=int(time.time()),
        )
        
        # Calculate per-worker rewards
        total_credits = int(epoch.total_credits)
        if total_credits == 0:
            return {}
        
        worker_credits = self.state.get_epoch_credits(epoch_id)
        worker_rewards = {}
        
        for worker_id, credits_str in worker_credits.items():
            credits = int(credits_str)
            # worker_share = inflow_for_workers * credits / total_credits
            # Round down to avoid overpayment
            reward = (inflow_for_workers * credits) // total_credits
            worker_rewards[worker_id] = str(reward)
        
        return worker_rewards

    def create_claim(
        self,
        epoch_id: int,
        worker_id: str,
        merkle_proof: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Create a claim for worker rewards.
        
        Args:
            epoch_id: Epoch ID
            worker_id: Worker ID
            merkle_proof: Optional merkle proof if using merkle tree
        
        Returns:
            Tuple of (claim_id, amount)
        """
        from uuid import uuid4
        
        # Check epoch is finalized
        epoch = self.state.get_epoch(epoch_id)
        if not epoch.finalized:
            raise ValueError(f"Epoch {epoch_id} not finalized")
        
        # Calculate worker share
        total_credits = int(epoch.total_credits)
        if total_credits == 0:
            return "", "0"
        
        worker_credits = self.state.get_worker_credits(epoch_id, worker_id)
        if int(worker_credits) == 0:
            return "", "0"
        
        inflow_for_workers = int(epoch.inflow_for_workers)
        credits = int(worker_credits)
        
        # Calculate reward (round down)
        reward = (inflow_for_workers * credits) // total_credits
        
        if reward == 0:
            return "", "0"
        
        # Create claim
        claim_id = f"claim_{uuid4().hex[:16]}"
        
        from aicf.protocol.state import WorkerClaim
        self.state.create_claim(WorkerClaim(
            claim_id=claim_id,
            epoch_id=epoch_id,
            worker_id=worker_id,
            amount=str(reward),
            merkle_proof=merkle_proof,
            status="PENDING",
        ))
        
        return claim_id, str(reward)

    def process_claim(
        self,
        claim_id: str,
        tx_hash: str,
    ) -> None:
        """
        Mark a claim as paid.
        
        Args:
            claim_id: Claim ID
            tx_hash: Payment transaction hash
        """
        self.state.update_claim(
            claim_id,
            status="PAID",
            tx_hash=tx_hash,
        )

    def get_epoch_stats(self, epoch_id: int) -> Dict[str, any]:
        """
        Get epoch statistics.
        
        Args:
            epoch_id: Epoch ID
        
        Returns:
            Dict with epoch stats
        """
        epoch = self.state.get_epoch(epoch_id)
        split = self.get_reward_split()
        
        inflow_total = int(epoch.inflow_total)
        inflow_for_workers = (inflow_total * split.gpu_workers_bp) // 10000
        inflow_for_treasury = (inflow_total * split.treasury_bp) // 10000
        inflow_for_dev = (inflow_total * split.dev_bp) // 10000
        inflow_for_burn = (inflow_total * split.burn_bp) // 10000
        
        return {
            "epoch_id": epoch.epoch_id,
            "start_height": epoch.start_height,
            "end_height": epoch.end_height,
            "inflow_total": epoch.inflow_total,
            "inflow_ena": epoch.inflow_ena,
            "inflow_other": epoch.inflow_other,
            "inflow_for_workers": str(inflow_for_workers),
            "inflow_for_treasury": str(inflow_for_treasury),
            "inflow_for_dev": str(inflow_for_dev),
            "inflow_for_burn": str(inflow_for_burn),
            "total_credits": epoch.total_credits,
            "finalized": epoch.finalized,
            "finalized_at": epoch.finalized_at,
            "worker_count": len(self.state.get_epoch_credits(epoch.epoch_id)),
        }
