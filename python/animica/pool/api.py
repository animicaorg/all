"""
HTTP API for the mining pool.

Provides read-only JSON endpoints for pool and miner statistics.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .db import PoolDatabase
from .stats import StatsTracker


def create_pool_api(
    db: PoolDatabase,
    stats: StatsTracker,
    *,
    logger: Optional[logging.Logger] = None,
) -> FastAPI:
    """
    Create FastAPI app for pool API.
    
    Args:
        db: Pool database
        stats: Statistics tracker
        logger: Optional logger
    
    Returns:
        FastAPI application
    """
    log = logger or logging.getLogger("animica.pool.api")
    
    app = FastAPI(
        title="Animica Mining Pool API",
        description="JSON API for pool statistics and miner information",
        version="0.1.0",
    )

    @app.get("/api/pool/status")
    def get_pool_status():
        """Get pool status and statistics."""
        try:
            pool_stats = stats.get_pool_stats()
            
            return {
                "status": "running",
                "miners": {
                    "total": pool_stats.total_miners,
                    "active": pool_stats.active_miners,
                },
                "workers": {
                    "total": pool_stats.total_workers,
                    "active": pool_stats.active_workers,
                },
                "hashrate": pool_stats.pool_hashrate,
                "shares": {
                    "total": pool_stats.total_shares,
                    "accepted": pool_stats.accepted_shares,
                    "rejected": pool_stats.rejected_shares,
                    "per_minute": pool_stats.shares_per_minute,
                },
                "blocks": {
                    "found": pool_stats.blocks_found,
                    "confirmed": pool_stats.blocks_confirmed,
                    "orphaned": pool_stats.blocks_orphaned,
                    "last_at": pool_stats.last_block_at.isoformat() if pool_stats.last_block_at else None,
                },
                "luck_percent": pool_stats.luck_percent,
                "payouts": {
                    "total_paid": pool_stats.total_paid,
                    "unpaid_balances": pool_stats.unpaid_balances,
                },
            }
        except Exception as e:
            log.error(f"Error getting pool status: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/pool/blocks")
    def get_pool_blocks(limit: int = 50):
        """Get recent blocks found by the pool."""
        try:
            if limit < 1 or limit > 100:
                raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
            
            rows = db.fetchall(
                """
                SELECT id, height, hash, found_at, state, confirmations, coinbase_value, orphaned
                FROM blocks
                ORDER BY found_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            
            blocks = []
            for row in rows:
                blocks.append({
                    "id": row["id"],
                    "height": row["height"],
                    "hash": row["hash"],
                    "found_at": row["found_at"],
                    "state": row["state"],
                    "confirmations": row["confirmations"],
                    "reward": row["coinbase_value"],
                    "orphaned": bool(row["orphaned"]),
                })
            
            return {"blocks": blocks}
        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Error getting blocks: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/pool/miners")
    def get_pool_miners(limit: int = 50):
        """Get list of miners."""
        try:
            if limit < 1 or limit > 100:
                raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
            
            rows = db.fetchall(
                """
                SELECT id, payout_address, created_at, last_seen_at
                FROM miners
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            
            miners = []
            for row in rows:
                miners.append({
                    "id": row["id"],
                    "payout_address": row["payout_address"],
                    "created_at": row["created_at"],
                    "last_seen_at": row["last_seen_at"],
                })
            
            return {"miners": miners}
        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Error getting miners: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/miner/{address}/stats")
    def get_miner_stats(address: str):
        """Get statistics for a specific miner by payout address."""
        try:
            # Find miner by address
            miner_row = db.fetchone(
                "SELECT id FROM miners WHERE payout_address = ?",
                (address,),
            )
            
            if not miner_row:
                raise HTTPException(status_code=404, detail="Miner not found")
            
            miner_id = miner_row["id"]
            miner_stats = stats.get_miner_stats(miner_id)
            
            if not miner_stats:
                raise HTTPException(status_code=404, detail="Miner stats not found")
            
            return {
                "miner_id": miner_stats.miner_id,
                "payout_address": miner_stats.payout_address,
                "hashrate": miner_stats.hashrate_ema,
                "shares": {
                    "total": miner_stats.total_shares,
                    "accepted": miner_stats.accepted_shares,
                    "rejected": miner_stats.rejected_shares,
                    "stale": miner_stats.stale_shares,
                    "invalid": miner_stats.invalid_shares,
                },
                "work": miner_stats.total_work,
                "last_share_at": miner_stats.last_share_at.isoformat() if miner_stats.last_share_at else None,
                "blocks_found": miner_stats.blocks_found,
                "balance": {
                    "unpaid": miner_stats.balance_unpaid,
                    "paid": miner_stats.total_paid,
                    "total_earned": miner_stats.total_earned,
                },
            }
        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Error getting miner stats: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/miner/{address}/balance")
    def get_miner_balance(address: str):
        """Get balance for a specific miner."""
        try:
            balance_row = db.fetchone(
                "SELECT immature, mature, paid_total FROM balances WHERE payout_address = ?",
                (address,),
            )
            
            if not balance_row:
                return {
                    "payout_address": address,
                    "immature": 0,
                    "mature": 0,
                    "paid_total": 0,
                    "total_unpaid": 0,
                }
            
            immature = balance_row["immature"] or 0
            mature = balance_row["mature"] or 0
            paid_total = balance_row["paid_total"] or 0
            
            return {
                "payout_address": address,
                "immature": immature,
                "mature": mature,
                "paid_total": paid_total,
                "total_unpaid": immature + mature,
            }
        except Exception as e:
            log.error(f"Error getting miner balance: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/miner/{address}/payouts")
    def get_miner_payouts(address: str, limit: int = 50):
        """Get payout history for a specific miner."""
        try:
            if limit < 1 or limit > 100:
                raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
            
            rows = db.fetchall(
                """
                SELECT p.id, p.created_at, p.txid, p.state, pi.amount
                FROM payouts p
                JOIN payout_items pi ON p.id = pi.payout_id
                WHERE pi.payout_address = ?
                ORDER BY p.created_at DESC
                LIMIT ?
                """,
                (address, limit),
            )
            
            payouts = []
            for row in rows:
                payouts.append({
                    "payout_id": row["id"],
                    "created_at": row["created_at"],
                    "txid": row["txid"],
                    "state": row["state"],
                    "amount": row["amount"],
                })
            
            return {"payouts": payouts}
        except HTTPException:
            raise
        except Exception as e:
            log.error(f"Error getting miner payouts: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/health")
    def health_check():
        """Health check endpoint."""
        return {"status": "healthy"}

    return app
