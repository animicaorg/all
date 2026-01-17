"""
Share recording and miner management for the mining pool.

Handles persisting shares to the database and managing miner/worker identities.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from .db import PoolDatabase
from .models import Miner, Share, Worker
from .share_validator import ShareSubmission, ShareValidationResult


class ShareRecorder:
    """
    Records shares to the database and manages miner/worker identities.
    """

    def __init__(
        self,
        db: PoolDatabase,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._db = db
        self._log = logger or logging.getLogger("animica.pool.share_recorder")
        
        # Cache for miner lookups
        self._miner_cache: dict[str, UUID] = {}  # address -> miner_id
        self._worker_cache: dict[tuple[UUID, str], int] = {}  # (miner_id, name) -> worker_id

    def record_share(
        self,
        submission: ShareSubmission,
        validation: ShareValidationResult,
    ) -> int:
        """
        Record a share submission to the database.
        
        Args:
            submission: Original share submission
            validation: Validation result
        
        Returns:
            Share ID
        """
        # Get or create miner
        miner_id = self._get_or_create_miner(submission.miner_address)
        
        # Get or create worker
        worker_id = self._get_or_create_worker(
            miner_id,
            submission.worker_name,
            ip=None,  # TODO: Get from connection
        )
        
        # Insert share
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO shares 
                (miner_id, worker_id, height, job_id, difficulty, work, accepted, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(miner_id),
                    worker_id,
                    submission.height,
                    submission.job_id,
                    submission.difficulty,
                    validation.work_weight,
                    1 if validation.accepted else 0,
                    validation.reason,
                    datetime.utcnow(),
                ),
            )
            share_id = cursor.lastrowid
        
        self._log.debug(
            f"Recorded share {share_id} from miner {submission.miner_address[:12]} "
            f"(accepted={validation.accepted}, work={validation.work_weight})"
        )
        
        return share_id

    def _get_or_create_miner(self, payout_address: str) -> UUID:
        """Get miner ID, creating record if needed."""
        # Check cache
        if payout_address in self._miner_cache:
            return self._miner_cache[payout_address]
        
        # Check database
        row = self._db.fetchone(
            "SELECT id FROM miners WHERE payout_address = ?",
            (payout_address,),
        )
        
        if row:
            miner_id = UUID(row["id"])
            self._miner_cache[payout_address] = miner_id
            return miner_id
        
        # Create new miner
        miner = Miner.create(payout_address)
        
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO miners (id, payout_address, created_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(miner.id),
                    miner.payout_address,
                    miner.created_at,
                    miner.last_seen_at,
                ),
            )
        
        self._miner_cache[payout_address] = miner.id
        self._log.info(f"Created new miner: {payout_address[:12]}... (ID: {miner.id})")
        
        return miner.id

    def _get_or_create_worker(
        self,
        miner_id: UUID,
        worker_name: str,
        ip: Optional[str] = None,
    ) -> int:
        """Get worker ID, creating record if needed."""
        cache_key = (miner_id, worker_name)
        
        # Check cache
        if cache_key in self._worker_cache:
            # Update last_seen
            self._db.execute(
                "UPDATE workers SET last_seen_at = ? WHERE id = ?",
                (datetime.utcnow(), self._worker_cache[cache_key]),
            )
            self._db.commit()
            return self._worker_cache[cache_key]
        
        # Check database
        row = self._db.fetchone(
            "SELECT id FROM workers WHERE miner_id = ? AND name = ?",
            (str(miner_id), worker_name),
        )
        
        if row:
            worker_id = row["id"]
            self._worker_cache[cache_key] = worker_id
            
            # Update last_seen
            self._db.execute(
                "UPDATE workers SET last_seen_at = ? WHERE id = ?",
                (datetime.utcnow(), worker_id),
            )
            self._db.commit()
            
            return worker_id
        
        # Create new worker
        now = datetime.utcnow()
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO workers (miner_id, name, connected_at, last_seen_at, ip)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(miner_id), worker_name, now, now, ip),
            )
            worker_id = cursor.lastrowid
        
        self._worker_cache[cache_key] = worker_id
        self._log.info(f"Created new worker: {worker_name} for miner {miner_id}")
        
        return worker_id

    def update_miner_last_seen(self, payout_address: str) -> None:
        """Update miner's last_seen_at timestamp."""
        self._db.execute(
            "UPDATE miners SET last_seen_at = ? WHERE payout_address = ?",
            (datetime.utcnow(), payout_address),
        )
        self._db.commit()

    def get_miner_by_address(self, payout_address: str) -> Optional[Miner]:
        """Get miner record by payout address."""
        row = self._db.fetchone(
            "SELECT * FROM miners WHERE payout_address = ?",
            (payout_address,),
        )
        
        if not row:
            return None
        
        return Miner(
            id=UUID(row["id"]),
            payout_address=row["payout_address"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            settings_json=row["settings_json"],
        )

    def get_share_stats(self, miner_id: UUID) -> dict:
        """Get share statistics for a miner."""
        row = self._db.fetchone(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) as accepted,
                SUM(CASE WHEN accepted = 0 THEN 1 ELSE 0 END) as rejected,
                SUM(CASE WHEN accepted = 1 THEN work ELSE 0 END) as total_work
            FROM shares
            WHERE miner_id = ?
            """,
            (str(miner_id),),
        )
        
        if not row:
            return {
                "total": 0,
                "accepted": 0,
                "rejected": 0,
                "total_work": 0,
            }
        
        return {
            "total": row["total"] or 0,
            "accepted": row["accepted"] or 0,
            "rejected": row["rejected"] or 0,
            "total_work": row["total_work"] or 0,
        }
