"""
AICF Storage Credits Module

This module calculates and manages storage credits for DA providers based on:
- Storage volume (GB-months)
- Audit results (passes/failures)
- Bandwidth consumption
- Reliability score

Credits are stored in SQLite and can be claimed through the AICF treasury.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from aicf.db import db_path

# Credit calculation constants
STORAGE_RATE_PER_GB_MONTH = 100  # credits per GB per month
AUDIT_PASS_BONUS = 10  # credits per successful audit
BANDWIDTH_RATE_PER_GB = 5  # credits per GB bandwidth


@dataclass
class StorageCreditRecord:
    """Record of storage credits earned by a provider for a period."""

    provider_id: bytes  # 32-byte provider ID
    period: str  # YYYY-MM format
    gb_stored: float  # Average GB stored during period
    audits_passed: int  # Number of successful audits
    audits_failed: int  # Number of failed audits
    bandwidth_gb: float  # Total bandwidth served in GB
    reliability_score: int  # 0-10000
    credits_earned: int  # Total credits calculated
    settled: bool = False  # Whether credits have been settled
    settled_at: Optional[int] = None  # Unix timestamp when settled


class StorageCreditsDB:
    """
    Storage credits database manager.

    Stores credit records and provides querying/settlement operations.
    """

    def __init__(self, db_path_override: Optional[Path] = None):
        """
        Initialize storage credits database.

        Args:
            db_path_override: Optional override for database path.
                            Defaults to ~/.animica/aicf/storage_credits.db
        """
        if db_path_override:
            self.db_path = Path(db_path_override)
        else:
            self.db_path = db_path("storage_credits.db", create=True)

        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS storage_credits (
                    provider_id BLOB NOT NULL,
                    period TEXT NOT NULL,
                    gb_stored REAL NOT NULL,
                    audits_passed INTEGER NOT NULL,
                    audits_failed INTEGER NOT NULL,
                    bandwidth_gb REAL NOT NULL,
                    reliability_score INTEGER NOT NULL,
                    credits_earned INTEGER NOT NULL,
                    settled INTEGER NOT NULL DEFAULT 0,
                    settled_at INTEGER,
                    PRIMARY KEY (provider_id, period)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def record_credits(
        self,
        provider_id: bytes,
        period: str,
        gb_stored: float,
        audits_passed: int,
        audits_failed: int,
        bandwidth_gb: float,
        reliability_score: int,
        credits_earned: int,
    ) -> None:
        """
        Record or update storage credits for a provider period.

        Args:
            provider_id: Provider identifier (32 bytes)
            period: Period in YYYY-MM format
            gb_stored: Average GB stored
            audits_passed: Number of successful audits
            audits_failed: Number of failed audits
            bandwidth_gb: Total bandwidth in GB
            reliability_score: Reliability score (0-10000)
            credits_earned: Total credits calculated
        """
        if len(provider_id) != 32:
            raise ValueError("provider_id must be 32 bytes")
        if not (0 <= reliability_score <= 10000):
            raise ValueError("reliability_score must be 0-10000")

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                INSERT INTO storage_credits
                    (provider_id, period, gb_stored, audits_passed, audits_failed,
                     bandwidth_gb, reliability_score, credits_earned, settled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(provider_id, period) DO UPDATE SET
                    gb_stored = excluded.gb_stored,
                    audits_passed = excluded.audits_passed,
                    audits_failed = excluded.audits_failed,
                    bandwidth_gb = excluded.bandwidth_gb,
                    reliability_score = excluded.reliability_score,
                    credits_earned = excluded.credits_earned
                """,
                (
                    provider_id,
                    period,
                    gb_stored,
                    audits_passed,
                    audits_failed,
                    bandwidth_gb,
                    reliability_score,
                    credits_earned,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_credits(
        self, provider_id: bytes, period: str
    ) -> Optional[StorageCreditRecord]:
        """
        Get credit record for a provider in a specific period.

        Args:
            provider_id: Provider identifier
            period: Period in YYYY-MM format

        Returns:
            StorageCreditRecord if found, None otherwise
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """
                SELECT provider_id, period, gb_stored, audits_passed, audits_failed,
                       bandwidth_gb, reliability_score, credits_earned, settled, settled_at
                FROM storage_credits
                WHERE provider_id = ? AND period = ?
                """,
                (provider_id, period),
            )
            row = cursor.fetchone()
            if row:
                return StorageCreditRecord(
                    provider_id=row[0],
                    period=row[1],
                    gb_stored=row[2],
                    audits_passed=row[3],
                    audits_failed=row[4],
                    bandwidth_gb=row[5],
                    reliability_score=row[6],
                    credits_earned=row[7],
                    settled=bool(row[8]),
                    settled_at=row[9],
                )
            return None
        finally:
            conn.close()

    def list_provider_credits(
        self, provider_id: bytes, settled: Optional[bool] = None
    ) -> List[StorageCreditRecord]:
        """
        List all credit records for a provider.

        Args:
            provider_id: Provider identifier
            settled: Filter by settlement status (None for all)

        Returns:
            List of credit records
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            if settled is None:
                cursor = conn.execute(
                    """
                    SELECT provider_id, period, gb_stored, audits_passed, audits_failed,
                           bandwidth_gb, reliability_score, credits_earned, settled, settled_at
                    FROM storage_credits
                    WHERE provider_id = ?
                    ORDER BY period DESC
                    """,
                    (provider_id,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT provider_id, period, gb_stored, audits_passed, audits_failed,
                           bandwidth_gb, reliability_score, credits_earned, settled, settled_at
                    FROM storage_credits
                    WHERE provider_id = ? AND settled = ?
                    ORDER BY period DESC
                    """,
                    (provider_id, int(settled)),
                )

            records = []
            for row in cursor.fetchall():
                records.append(
                    StorageCreditRecord(
                        provider_id=row[0],
                        period=row[1],
                        gb_stored=row[2],
                        audits_passed=row[3],
                        audits_failed=row[4],
                        bandwidth_gb=row[5],
                        reliability_score=row[6],
                        credits_earned=row[7],
                        settled=bool(row[8]),
                        settled_at=row[9],
                    )
                )
            return records
        finally:
            conn.close()

    def mark_settled(self, provider_id: bytes, period: str) -> None:
        """
        Mark credits as settled for a provider period.

        Args:
            provider_id: Provider identifier
            period: Period in YYYY-MM format
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                UPDATE storage_credits
                SET settled = 1, settled_at = ?
                WHERE provider_id = ? AND period = ?
                """,
                (int(time.time()), provider_id, period),
            )
            conn.commit()
        finally:
            conn.close()

    def get_total_claimable(self, provider_id: bytes) -> int:
        """
        Get total claimable (unsettled) credits for a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            Total credits that can be claimed
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """
                SELECT SUM(credits_earned)
                FROM storage_credits
                WHERE provider_id = ? AND settled = 0
                """,
                (provider_id,),
            )
            result = cursor.fetchone()[0]
            return result if result is not None else 0
        finally:
            conn.close()


def calculate_storage_credits(
    provider_id: bytes,
    gb_stored: float,
    audit_pass_count: int,
    audit_fail_count: int,
    bandwidth_gb: float,
    reliability_score: int,
) -> int:
    """
    Calculate AICF credits for a storage provider.

    Formula:
    base_credits = gb_stored * STORAGE_RATE_PER_GB_MONTH
    audit_bonus = audit_pass_count * AUDIT_PASS_BONUS
    bandwidth_credits = bandwidth_gb * BANDWIDTH_RATE_PER_GB
    reliability_multiplier = reliability_score / 10000.0

    total = (base_credits + audit_bonus + bandwidth_credits) * reliability_multiplier

    Args:
        provider_id: Provider identifier (32 bytes, not used in calc but for validation)
        gb_stored: Average GB stored during period
        audit_pass_count: Number of successful audits
        audit_fail_count: Number of failed audits (not currently penalized)
        bandwidth_gb: Total bandwidth served in GB
        reliability_score: Reliability score (0-10000, represents 0-100%)

    Returns:
        Total credits earned (integer)
    """
    if len(provider_id) != 32:
        raise ValueError("provider_id must be 32 bytes")
    if gb_stored < 0:
        raise ValueError("gb_stored must be non-negative")
    if audit_pass_count < 0 or audit_fail_count < 0:
        raise ValueError("audit counts must be non-negative")
    if bandwidth_gb < 0:
        raise ValueError("bandwidth_gb must be non-negative")
    if not (0 <= reliability_score <= 10000):
        raise ValueError("reliability_score must be 0-10000")

    # Calculate base components
    base_credits = gb_stored * STORAGE_RATE_PER_GB_MONTH
    audit_bonus = audit_pass_count * AUDIT_PASS_BONUS
    bandwidth_credits = bandwidth_gb * BANDWIDTH_RATE_PER_GB

    # Apply reliability multiplier
    reliability_multiplier = reliability_score / 10000.0
    total = (base_credits + audit_bonus + bandwidth_credits) * reliability_multiplier

    return int(total)


__all__ = [
    "STORAGE_RATE_PER_GB_MONTH",
    "AUDIT_PASS_BONUS",
    "BANDWIDTH_RATE_PER_GB",
    "StorageCreditRecord",
    "StorageCreditsDB",
    "calculate_storage_credits",
]
