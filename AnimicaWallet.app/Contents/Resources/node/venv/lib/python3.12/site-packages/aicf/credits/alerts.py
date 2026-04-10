"""
AICF Provider Alerts Module

This module implements a provider alert system that monitors for:
- Low disk space
- Failed audits
- Low reliability scores
- Sync backlog
- High egress bandwidth

Alerts are stored in SQLite and can be queried/cleared.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional

from aicf.db import db_path


class AlertType(str, Enum):
    """Types of provider alerts."""

    LOW_DISK_FREE = "LOW_DISK_FREE"  # < 10% free space
    FAILED_AUDITS = "FAILED_AUDITS"  # > 5 failed audits in last hour
    LOW_RELIABILITY = "LOW_RELIABILITY"  # score < 3000
    SYNC_BACKLOG = "SYNC_BACKLOG"  # > 100 unsynced blobs
    HIGH_EGRESS = "HIGH_EGRESS"  # > plan bandwidth limit


@dataclass
class Alert:
    """Provider alert record."""

    provider_id: bytes
    alert_type: AlertType
    severity: str  # "warning", "critical"
    message: str
    created_at: int  # Unix timestamp
    cleared_at: Optional[int] = None  # Unix timestamp when cleared
    metadata: Optional[str] = None  # JSON metadata


class AlertsDB:
    """
    Database for storing provider alerts.

    Tracks active and historical alerts for monitoring and debugging.
    """

    def __init__(self, db_path_override: Optional[Path] = None):
        """
        Initialize alerts database.

        Args:
            db_path_override: Optional override for database path.
        """
        if db_path_override:
            self.db_path = Path(db_path_override)
        else:
            self.db_path = db_path("provider_alerts.db", create=True)

        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id BLOB NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    cleared_at INTEGER,
                    metadata TEXT
                )
                """
            )
            # Index for faster queries
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_provider_active
                ON alerts(provider_id, cleared_at)
                """
            )
            conn.commit()
        finally:
            conn.close()

    def create_alert(
        self,
        provider_id: bytes,
        alert_type: AlertType,
        severity: str,
        message: str,
        metadata: Optional[str] = None,
    ) -> int:
        """
        Create a new alert.

        Args:
            provider_id: Provider identifier (32 bytes)
            alert_type: Type of alert
            severity: "warning" or "critical"
            message: Human-readable alert message
            metadata: Optional JSON metadata

        Returns:
            Alert ID
        """
        if len(provider_id) != 32:
            raise ValueError("provider_id must be 32 bytes")
        if severity not in ("warning", "critical"):
            raise ValueError("severity must be 'warning' or 'critical'")

        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """
                INSERT INTO alerts
                    (provider_id, alert_type, severity, message, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    alert_type.value,
                    severity,
                    message,
                    int(time.time()),
                    metadata,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_active_alerts(
        self, provider_id: bytes, alert_type: Optional[AlertType] = None
    ) -> List[Alert]:
        """
        Get active (uncleared) alerts for a provider.

        Args:
            provider_id: Provider identifier
            alert_type: Optional filter by alert type

        Returns:
            List of active alerts
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            if alert_type is None:
                cursor = conn.execute(
                    """
                    SELECT provider_id, alert_type, severity, message,
                           created_at, cleared_at, metadata
                    FROM alerts
                    WHERE provider_id = ? AND cleared_at IS NULL
                    ORDER BY created_at DESC
                    """,
                    (provider_id,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT provider_id, alert_type, severity, message,
                           created_at, cleared_at, metadata
                    FROM alerts
                    WHERE provider_id = ? AND alert_type = ? AND cleared_at IS NULL
                    ORDER BY created_at DESC
                    """,
                    (provider_id, alert_type.value),
                )

            alerts = []
            for row in cursor.fetchall():
                alerts.append(
                    Alert(
                        provider_id=row[0],
                        alert_type=AlertType(row[1]),
                        severity=row[2],
                        message=row[3],
                        created_at=row[4],
                        cleared_at=row[5],
                        metadata=row[6],
                    )
                )
            return alerts
        finally:
            conn.close()

    def clear_alert(
        self, provider_id: bytes, alert_type: AlertType
    ) -> int:
        """
        Clear all active alerts of a specific type for a provider.

        Args:
            provider_id: Provider identifier
            alert_type: Type of alert to clear

        Returns:
            Number of alerts cleared
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """
                UPDATE alerts
                SET cleared_at = ?
                WHERE provider_id = ? AND alert_type = ? AND cleared_at IS NULL
                """,
                (int(time.time()), provider_id, alert_type.value),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def clear_all_alerts(self, provider_id: bytes) -> int:
        """
        Clear all active alerts for a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            Number of alerts cleared
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """
                UPDATE alerts
                SET cleared_at = ?
                WHERE provider_id = ? AND cleared_at IS NULL
                """,
                (int(time.time()), provider_id),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


def check_alerts(
    provider_id: bytes,
    disk_free_pct: float,
    failed_audits_last_hour: int,
    reliability_score: int,
    unsynced_blob_count: int,
    egress_gb: float,
    plan_bandwidth_limit_gb: Optional[float] = None,
    db_path_override: Optional[Path] = None,
) -> List[Alert]:
    """
    Check all alert conditions and create alerts as needed.

    This function evaluates provider metrics against thresholds and
    creates new alerts for any violations. It does not clear alerts;
    use clear_alert() explicitly for that.

    Args:
        provider_id: Provider identifier (32 bytes)
        disk_free_pct: Percentage of free disk space (0-100)
        failed_audits_last_hour: Number of failed audits in last hour
        reliability_score: Current reliability score (0-10000)
        unsynced_blob_count: Number of blobs not yet synced
        egress_gb: Egress bandwidth in GB
        plan_bandwidth_limit_gb: Optional bandwidth limit from plan
        db_path_override: Optional database path override

    Returns:
        List of newly created alerts
    """
    db = AlertsDB(db_path_override=db_path_override)
    new_alerts = []

    # Check LOW_DISK_FREE
    if disk_free_pct < 10.0:
        alert_id = db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.LOW_DISK_FREE,
            severity="critical" if disk_free_pct < 5.0 else "warning",
            message=f"Low disk space: {disk_free_pct:.1f}% free",
            metadata=f'{{"disk_free_pct": {disk_free_pct}}}',
        )
        new_alerts.append(
            Alert(
                provider_id=provider_id,
                alert_type=AlertType.LOW_DISK_FREE,
                severity="critical" if disk_free_pct < 5.0 else "warning",
                message=f"Low disk space: {disk_free_pct:.1f}% free",
                created_at=int(time.time()),
                metadata=f'{{"disk_free_pct": {disk_free_pct}}}',
            )
        )

    # Check FAILED_AUDITS
    if failed_audits_last_hour > 5:
        alert_id = db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.FAILED_AUDITS,
            severity="critical" if failed_audits_last_hour > 10 else "warning",
            message=f"High audit failure rate: {failed_audits_last_hour} failures in last hour",
            metadata=f'{{"failed_audits": {failed_audits_last_hour}}}',
        )
        new_alerts.append(
            Alert(
                provider_id=provider_id,
                alert_type=AlertType.FAILED_AUDITS,
                severity="critical" if failed_audits_last_hour > 10 else "warning",
                message=f"High audit failure rate: {failed_audits_last_hour} failures in last hour",
                created_at=int(time.time()),
                metadata=f'{{"failed_audits": {failed_audits_last_hour}}}',
            )
        )

    # Check LOW_RELIABILITY
    if reliability_score < 3000:
        alert_id = db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.LOW_RELIABILITY,
            severity="critical" if reliability_score < 1000 else "warning",
            message=f"Low reliability score: {reliability_score}/10000",
            metadata=f'{{"reliability_score": {reliability_score}}}',
        )
        new_alerts.append(
            Alert(
                provider_id=provider_id,
                alert_type=AlertType.LOW_RELIABILITY,
                severity="critical" if reliability_score < 1000 else "warning",
                message=f"Low reliability score: {reliability_score}/10000",
                created_at=int(time.time()),
                metadata=f'{{"reliability_score": {reliability_score}}}',
            )
        )

    # Check SYNC_BACKLOG
    if unsynced_blob_count > 100:
        alert_id = db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.SYNC_BACKLOG,
            severity="critical" if unsynced_blob_count > 500 else "warning",
            message=f"Sync backlog: {unsynced_blob_count} unsynced blobs",
            metadata=f'{{"unsynced_blobs": {unsynced_blob_count}}}',
        )
        new_alerts.append(
            Alert(
                provider_id=provider_id,
                alert_type=AlertType.SYNC_BACKLOG,
                severity="critical" if unsynced_blob_count > 500 else "warning",
                message=f"Sync backlog: {unsynced_blob_count} unsynced blobs",
                created_at=int(time.time()),
                metadata=f'{{"unsynced_blobs": {unsynced_blob_count}}}',
            )
        )

    # Check HIGH_EGRESS
    if plan_bandwidth_limit_gb is not None and egress_gb > plan_bandwidth_limit_gb:
        alert_id = db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.HIGH_EGRESS,
            severity="warning",
            message=f"High egress: {egress_gb:.2f} GB (limit: {plan_bandwidth_limit_gb:.2f} GB)",
            metadata=f'{{"egress_gb": {egress_gb}, "limit_gb": {plan_bandwidth_limit_gb}}}',
        )
        new_alerts.append(
            Alert(
                provider_id=provider_id,
                alert_type=AlertType.HIGH_EGRESS,
                severity="warning",
                message=f"High egress: {egress_gb:.2f} GB (limit: {plan_bandwidth_limit_gb:.2f} GB)",
                created_at=int(time.time()),
                metadata=f'{{"egress_gb": {egress_gb}, "limit_gb": {plan_bandwidth_limit_gb}}}',
            )
        )

    return new_alerts


def get_active_alerts(
    provider_id: bytes,
    alert_type: Optional[AlertType] = None,
    db_path_override: Optional[Path] = None,
) -> List[Alert]:
    """
    Get active alerts for a provider.

    Args:
        provider_id: Provider identifier
        alert_type: Optional filter by alert type
        db_path_override: Optional database path override

    Returns:
        List of active alerts
    """
    db = AlertsDB(db_path_override=db_path_override)
    return db.get_active_alerts(provider_id, alert_type)


def clear_alert(
    provider_id: bytes,
    alert_type: AlertType,
    db_path_override: Optional[Path] = None,
) -> int:
    """
    Clear all active alerts of a specific type.

    Args:
        provider_id: Provider identifier
        alert_type: Type of alert to clear
        db_path_override: Optional database path override

    Returns:
        Number of alerts cleared
    """
    db = AlertsDB(db_path_override=db_path_override)
    return db.clear_alert(provider_id, alert_type)


__all__ = [
    "AlertType",
    "Alert",
    "AlertsDB",
    "check_alerts",
    "get_active_alerts",
    "clear_alert",
]
