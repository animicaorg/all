"""
PPLNS (Pay Per Last N Shares) calculation and payout distribution.

Implements work-based PPLNS window selection and deterministic payout calculations.
All arithmetic uses integers (base units) to ensure determinism.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .db import PoolDatabase


@dataclass
class PPLNSWindow:
    """PPLNS window for a found block."""

    start_share_id: int
    end_share_id: int
    total_work: int  # Sum of work in window
    miner_work: dict[str, int]  # payout_address -> work


@dataclass
class PayoutDistribution:
    """Calculated payout distribution for a block."""

    block_id: int
    block_reward: int  # Base units
    pool_fee: int  # Base units
    distributable: int  # Base units (after fees)
    payouts: dict[str, int]  # payout_address -> amount (base units)
    dust: int  # Leftover dust from rounding


class PPLNSCalculator:
    """
    Calculates PPLNS payouts with work-based window selection.
    
    Window size is configured as a multiple of network difficulty.
    For example, window_work=2 means the window spans approximately 2 blocks
    worth of expected work.
    """

    def __init__(
        self,
        db: PoolDatabase,
        window_work_multiplier: int = 2,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._db = db
        self._window_work_multiplier = window_work_multiplier
        self._log = logger or logging.getLogger("animica.pool.pplns")

    def calculate_window(
        self,
        block_id: int,
        network_difficulty: float,
        end_share_id: int,
    ) -> PPLNSWindow:
        """
        Calculate PPLNS window for a found block.
        
        Args:
            block_id: Block ID
            network_difficulty: Network difficulty at time of block
            end_share_id: Last share ID to include (the finding share)
        
        Returns:
            PPLNSWindow with share range and miner work contributions
        """
        # Calculate target work for window
        # Work is stored as: difficulty * 1_000_000
        target_work = int(
            network_difficulty * self._window_work_multiplier * 1_000_000
        )
        
        self._log.debug(
            f"Calculating PPLNS window for block {block_id}: "
            f"target_work={target_work}, end_share={end_share_id}"
        )
        
        # Select shares backwards from end_share_id until we accumulate target_work
        shares = self._db.fetchall(
            """
            SELECT id, miner_id, work, created_at
            FROM shares
            WHERE id <= ? AND accepted = 1
            ORDER BY id DESC
            """,
            (end_share_id,),
        )
        
        if not shares:
            self._log.warning(f"No shares found for PPLNS window (block {block_id})")
            return PPLNSWindow(
                start_share_id=end_share_id,
                end_share_id=end_share_id,
                total_work=0,
                miner_work={},
            )
        
        # Accumulate work until we reach target
        accumulated_work = 0
        included_shares = []
        miner_work = {}
        
        for share in shares:
            share_id = share["id"]
            miner_id = share["miner_id"]
            work = share["work"]
            
            included_shares.append(share_id)
            accumulated_work += work
            
            # Get miner's payout address
            miner = self._db.fetchone(
                "SELECT payout_address FROM miners WHERE id = ?",
                (miner_id,),
            )
            
            if miner:
                payout_address = miner["payout_address"]
                miner_work[payout_address] = miner_work.get(payout_address, 0) + work
            
            # Stop when we've accumulated enough work
            if accumulated_work >= target_work:
                break
        
        start_share_id = min(included_shares)
        
        self._log.info(
            f"PPLNS window for block {block_id}: "
            f"shares {start_share_id} to {end_share_id}, "
            f"total_work={accumulated_work}, "
            f"miners={len(miner_work)}"
        )
        
        return PPLNSWindow(
            start_share_id=start_share_id,
            end_share_id=end_share_id,
            total_work=accumulated_work,
            miner_work=miner_work,
        )

    def calculate_payouts(
        self,
        window: PPLNSWindow,
        block_reward: int,
        pool_fee_percent: float,
    ) -> PayoutDistribution:
        """
        Calculate payout distribution from PPLNS window.
        
        Args:
            window: PPLNS window with miner work
            block_reward: Total block reward in base units
            pool_fee_percent: Pool fee percentage (e.g., 1.0 for 1%)
        
        Returns:
            PayoutDistribution with per-miner amounts
        """
        if window.total_work == 0:
            self._log.warning("Cannot calculate payouts: window has zero work")
            return PayoutDistribution(
                block_id=0,
                block_reward=block_reward,
                pool_fee=0,
                distributable=0,
                payouts={},
                dust=0,
            )
        
        # Calculate pool fee
        pool_fee = int((block_reward * pool_fee_percent) / 100.0)
        distributable = block_reward - pool_fee
        
        # Calculate per-miner payouts
        payouts = {}
        total_distributed = 0
        
        for payout_address, work in window.miner_work.items():
            # Payout = (miner_work / total_work) * distributable
            # Use integer math for determinism
            payout = (work * distributable) // window.total_work
            
            if payout > 0:
                payouts[payout_address] = payout
                total_distributed += payout
        
        # Calculate dust (leftover from rounding)
        dust = distributable - total_distributed
        
        self._log.info(
            f"Calculated payouts: "
            f"reward={block_reward}, "
            f"fee={pool_fee}, "
            f"distributable={distributable}, "
            f"distributed={total_distributed}, "
            f"dust={dust}, "
            f"miners={len(payouts)}"
        )
        
        return PayoutDistribution(
            block_id=0,  # Will be set by caller
            block_reward=block_reward,
            pool_fee=pool_fee,
            distributable=distributable,
            payouts=payouts,
            dust=dust,
        )

    def store_window(self, block_id: int, window: PPLNSWindow) -> None:
        """Store PPLNS window in block record."""
        self._db.execute(
            """
            UPDATE blocks
            SET pplns_window_start_share_id = ?, pplns_window_end_share_id = ?
            WHERE id = ?
            """,
            (window.start_share_id, window.end_share_id, block_id),
        )
        self._db.commit()
        
        self._log.debug(
            f"Stored PPLNS window for block {block_id}: "
            f"{window.start_share_id} to {window.end_share_id}"
        )
