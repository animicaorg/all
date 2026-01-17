"""
Statistics tracking and calculation for the mining pool.

Tracks pool-wide and per-miner statistics including:
- Hashrate (EMA-based)
- Shares (accepted/rejected/stale)
- Blocks found
- Luck percentage
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from .db import PoolDatabase
from .models import MinerStats, PoolStats, WorkerStats


class StatsTracker:
    """
    Tracks and calculates pool and miner statistics.
    
    Uses exponential moving average (EMA) for hashrate calculations
    to smooth out variance while remaining responsive to changes.
    """

    def __init__(
        self,
        db: PoolDatabase,
        ema_alpha: float = 0.1,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._db = db
        self._ema_alpha = ema_alpha
        self._log = logger or logging.getLogger("animica.pool.stats")
        
        # In-memory cache for hashrate EMA
        self._pool_hashrate_ema: float = 0.0
        self._miner_hashrate_ema: dict[str, float] = {}  # miner_id -> hashrate
        self._last_update = time.time()

    def update_hashrates(self, current_difficulty: float) -> None:
        """
        Update hashrate EMA for pool and miners.
        
        Args:
            current_difficulty: Current network difficulty
        """
        now = time.time()
        elapsed = now - self._last_update
        
        if elapsed < 1.0:
            return  # Update at most once per second
        
        # Get recent shares (last minute)
        cutoff = datetime.utcnow() - timedelta(seconds=60)
        
        rows = self._db.fetchall(
            """
            SELECT miner_id, SUM(work) as total_work, COUNT(*) as count
            FROM shares
            WHERE created_at >= ? AND accepted = 1
            GROUP BY miner_id
            """,
            (cutoff,),
        )
        
        # Calculate pool hashrate
        pool_work = sum(row["total_work"] for row in rows)
        pool_hashrate = pool_work / (60.0 * 1_000_000)  # Convert to hashes/sec
        
        # Update pool EMA
        if self._pool_hashrate_ema == 0:
            self._pool_hashrate_ema = pool_hashrate
        else:
            self._pool_hashrate_ema = (
                self._ema_alpha * pool_hashrate +
                (1 - self._ema_alpha) * self._pool_hashrate_ema
            )
        
        # Update per-miner EMA
        miner_work = {row["miner_id"]: row["total_work"] for row in rows}
        
        for miner_id, work in miner_work.items():
            hashrate = work / (60.0 * 1_000_000)
            
            if miner_id not in self._miner_hashrate_ema:
                self._miner_hashrate_ema[miner_id] = hashrate
            else:
                self._miner_hashrate_ema[miner_id] = (
                    self._ema_alpha * hashrate +
                    (1 - self._ema_alpha) * self._miner_hashrate_ema[miner_id]
                )
        
        self._last_update = now
        
        self._log.debug(
            f"Updated hashrates: pool={self._pool_hashrate_ema:.2f} H/s, "
            f"miners={len(self._miner_hashrate_ema)}"
        )

    def get_pool_stats(self) -> PoolStats:
        """Get pool-wide statistics."""
        # Count miners
        total_miners = self._db.fetchone("SELECT COUNT(*) as count FROM miners")["count"] or 0
        
        # Active miners (last hour)
        cutoff = datetime.utcnow() - timedelta(hours=1)
        active_miners = self._db.fetchone(
            "SELECT COUNT(*) as count FROM miners WHERE last_seen_at >= ?",
            (cutoff,),
        )["count"] or 0
        
        # Count workers
        total_workers = self._db.fetchone("SELECT COUNT(*) as count FROM workers")["count"] or 0
        active_workers = self._db.fetchone(
            "SELECT COUNT(*) as count FROM workers WHERE last_seen_at >= ?",
            (cutoff,),
        )["count"] or 0
        
        # Share stats
        share_stats = self._db.fetchone(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) as accepted,
                SUM(CASE WHEN accepted = 0 THEN 1 ELSE 0 END) as rejected
            FROM shares
            """
        )
        
        total_shares = share_stats["total"] or 0
        accepted_shares = share_stats["accepted"] or 0
        rejected_shares = share_stats["rejected"] or 0
        
        # Shares per minute (last 10 minutes)
        cutoff_10m = datetime.utcnow() - timedelta(minutes=10)
        recent_shares = self._db.fetchone(
            "SELECT COUNT(*) as count FROM shares WHERE created_at >= ?",
            (cutoff_10m,),
        )["count"] or 0
        shares_per_minute = recent_shares / 10.0
        
        # Block stats
        block_stats = self._db.fetchone(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN state = 'confirmed' OR state = 'paid' THEN 1 ELSE 0 END) as confirmed,
                SUM(CASE WHEN orphaned = 1 THEN 1 ELSE 0 END) as orphaned
            FROM blocks
            """
        )
        
        blocks_found = block_stats["total"] or 0
        blocks_confirmed = block_stats["confirmed"] or 0
        blocks_orphaned = block_stats["orphaned"] or 0
        
        # Last block time
        last_block_row = self._db.fetchone(
            "SELECT found_at FROM blocks ORDER BY found_at DESC LIMIT 1"
        )
        last_block_at = None
        if last_block_row:
            last_block_at = datetime.fromisoformat(last_block_row["found_at"])
        
        # Luck calculation (simplified)
        luck_percent = 100.0
        if blocks_confirmed > 0:
            # Luck = (actual shares / expected shares) * 100
            # Expected shares = difficulty * blocks
            # This is a simplified calculation
            pass
        
        # Payout stats
        payout_stats = self._db.fetchone(
            """
            SELECT SUM(total_amount) as total_paid
            FROM payouts
            WHERE state = 'confirmed'
            """
        )
        total_paid = payout_stats["total_paid"] or 0
        
        # Unpaid balances
        balance_stats = self._db.fetchone(
            "SELECT SUM(immature + mature) as unpaid FROM balances"
        )
        unpaid_balances = balance_stats["unpaid"] or 0
        
        return PoolStats(
            total_miners=total_miners,
            active_miners=active_miners,
            total_workers=total_workers,
            active_workers=active_workers,
            pool_hashrate=self._pool_hashrate_ema,
            shares_per_minute=shares_per_minute,
            total_shares=total_shares,
            accepted_shares=accepted_shares,
            rejected_shares=rejected_shares,
            blocks_found=blocks_found,
            blocks_confirmed=blocks_confirmed,
            blocks_orphaned=blocks_orphaned,
            last_block_at=last_block_at,
            luck_percent=luck_percent,
            total_paid=total_paid,
            unpaid_balances=unpaid_balances,
        )

    def get_miner_stats(self, miner_id: str) -> Optional[MinerStats]:
        """Get statistics for a specific miner."""
        # Get miner
        miner_row = self._db.fetchone(
            "SELECT payout_address FROM miners WHERE id = ?",
            (miner_id,),
        )
        
        if not miner_row:
            return None
        
        payout_address = miner_row["payout_address"]
        
        # Share stats
        share_stats = self._db.fetchone(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) as accepted,
                SUM(CASE WHEN accepted = 0 THEN 1 ELSE 0 END) as rejected,
                SUM(CASE WHEN accepted = 0 AND reason = 'stale' THEN 1 ELSE 0 END) as stale,
                SUM(CASE WHEN accepted = 0 AND reason != 'stale' THEN 1 ELSE 0 END) as invalid,
                SUM(CASE WHEN accepted = 1 THEN work ELSE 0 END) as total_work,
                MAX(created_at) as last_share_at
            FROM shares
            WHERE miner_id = ?
            """,
            (miner_id,),
        )
        
        total_shares = share_stats["total"] or 0
        accepted_shares = share_stats["accepted"] or 0
        rejected_shares = share_stats["rejected"] or 0
        stale_shares = share_stats["stale"] or 0
        invalid_shares = share_stats["invalid"] or 0
        total_work = share_stats["total_work"] or 0
        
        last_share_at = None
        if share_stats["last_share_at"]:
            last_share_at = datetime.fromisoformat(share_stats["last_share_at"])
        
        # Hashrate EMA
        hashrate_ema = self._miner_hashrate_ema.get(miner_id, 0.0)
        
        # Blocks found
        blocks_found = self._db.fetchone(
            "SELECT COUNT(*) as count FROM blocks WHERE finder_miner_id = ?",
            (miner_id,),
        )["count"] or 0
        
        # Earnings
        balance_row = self._db.fetchone(
            "SELECT immature + mature as unpaid, paid_total FROM balances WHERE payout_address = ?",
            (payout_address,),
        )
        
        balance_unpaid = 0
        total_paid = 0
        if balance_row:
            balance_unpaid = balance_row["unpaid"] or 0
            total_paid = balance_row["paid_total"] or 0
        
        total_earned = balance_unpaid + total_paid
        
        return MinerStats(
            miner_id=miner_id,
            payout_address=payout_address,
            total_shares=total_shares,
            accepted_shares=accepted_shares,
            rejected_shares=rejected_shares,
            stale_shares=stale_shares,
            invalid_shares=invalid_shares,
            total_work=total_work,
            hashrate_ema=hashrate_ema,
            last_share_at=last_share_at,
            blocks_found=blocks_found,
            total_earned=total_earned,
            total_paid=total_paid,
            balance_unpaid=balance_unpaid,
        )
