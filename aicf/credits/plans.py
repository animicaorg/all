"""
AICF Provider Plans Module

This module defines predefined storage provider plans with capacity,
heartbeat intervals, audit targets, and configuration settings.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from aicf.db import db_path

# Built-in provider plans
PLANS: Dict[str, Dict[str, object]] = {
    "starter-100gb": {
        "capacity": 107374182400,  # 100 GB
        "heartbeat_interval": 300,  # 5 minutes
        "audit_target": 24,  # audits per day
        "port": 9090,
    },
    "serious-1tb": {
        "capacity": 1099511627776,  # 1 TB
        "heartbeat_interval": 180,  # 3 minutes
        "audit_target": 48,
        "port": 9090,
    },
    "datacenter-10tb": {
        "capacity": 10995116277760,  # 10 TB
        "heartbeat_interval": 60,  # 1 minute
        "audit_target": 96,
        "port": 9090,
    },
}


@dataclass
class PlanInfo:
    """Provider plan information."""

    name: str
    capacity_bytes: int
    heartbeat_interval_seconds: int
    audit_target_per_day: int
    port: int


class PlanError(Exception):
    """Base error for plan operations."""


class PlanNotFoundError(PlanError):
    """Raised when a plan is not found."""


class ProviderPlansDB:
    """
    Database for storing provider plan assignments.

    Tracks which plan each provider is using and when it was applied.
    """

    def __init__(self, db_path_override: Optional[Path] = None):
        """
        Initialize provider plans database.

        Args:
            db_path_override: Optional override for database path.
        """
        if db_path_override:
            self.db_path = Path(db_path_override)
        else:
            self.db_path = db_path("provider_plans.db", create=True)

        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_plans (
                    provider_id BLOB PRIMARY KEY,
                    plan_name TEXT NOT NULL,
                    capacity_bytes INTEGER NOT NULL,
                    heartbeat_interval INTEGER NOT NULL,
                    audit_target INTEGER NOT NULL,
                    port INTEGER NOT NULL,
                    applied_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def set_plan(
        self, provider_id: bytes, plan_name: str, applied_at: int
    ) -> None:
        """
        Set or update plan for a provider.

        Args:
            provider_id: Provider identifier (32 bytes)
            plan_name: Name of the plan to apply
            applied_at: Unix timestamp when plan was applied

        Raises:
            PlanNotFoundError: If plan_name is not a valid plan
        """
        if plan_name not in PLANS:
            raise PlanNotFoundError(f"Plan '{plan_name}' not found")

        if len(provider_id) != 32:
            raise ValueError("provider_id must be 32 bytes")

        plan = PLANS[plan_name]

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                INSERT INTO provider_plans
                    (provider_id, plan_name, capacity_bytes, heartbeat_interval,
                     audit_target, port, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    plan_name = excluded.plan_name,
                    capacity_bytes = excluded.capacity_bytes,
                    heartbeat_interval = excluded.heartbeat_interval,
                    audit_target = excluded.audit_target,
                    port = excluded.port,
                    applied_at = excluded.applied_at
                """,
                (
                    provider_id,
                    plan_name,
                    plan["capacity"],
                    plan["heartbeat_interval"],
                    plan["audit_target"],
                    plan["port"],
                    applied_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_plan(self, provider_id: bytes) -> Optional[Dict[str, object]]:
        """
        Get current plan for a provider.

        Args:
            provider_id: Provider identifier

        Returns:
            Dictionary with plan details, or None if no plan assigned
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                """
                SELECT plan_name, capacity_bytes, heartbeat_interval,
                       audit_target, port, applied_at
                FROM provider_plans
                WHERE provider_id = ?
                """,
                (provider_id,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "plan_name": row[0],
                    "capacity": row[1],
                    "heartbeat_interval": row[2],
                    "audit_target": row[3],
                    "port": row[4],
                    "applied_at": row[5],
                }
            return None
        finally:
            conn.close()


def apply_plan(
    provider_id: bytes,
    plan_name: str,
    db_path_override: Optional[Path] = None,
) -> PlanInfo:
    """
    Apply a plan to a provider.

    Args:
        provider_id: Provider identifier (32 bytes)
        plan_name: Name of the plan to apply
        db_path_override: Optional database path override

    Returns:
        PlanInfo object with applied settings

    Raises:
        PlanNotFoundError: If plan_name is not valid
    """
    if plan_name not in PLANS:
        raise PlanNotFoundError(f"Plan '{plan_name}' not found")

    plan = PLANS[plan_name]
    db = ProviderPlansDB(db_path_override=db_path_override)

    import time

    db.set_plan(provider_id, plan_name, int(time.time()))

    return PlanInfo(
        name=plan_name,
        capacity_bytes=int(plan["capacity"]),
        heartbeat_interval_seconds=int(plan["heartbeat_interval"]),
        audit_target_per_day=int(plan["audit_target"]),
        port=int(plan["port"]),
    )


def get_plan_info(plan_name: str) -> PlanInfo:
    """
    Get information about a plan without applying it.

    Args:
        plan_name: Name of the plan

    Returns:
        PlanInfo object with plan details

    Raises:
        PlanNotFoundError: If plan_name is not valid
    """
    if plan_name not in PLANS:
        raise PlanNotFoundError(f"Plan '{plan_name}' not found")

    plan = PLANS[plan_name]
    return PlanInfo(
        name=plan_name,
        capacity_bytes=int(plan["capacity"]),
        heartbeat_interval_seconds=int(plan["heartbeat_interval"]),
        audit_target_per_day=int(plan["audit_target"]),
        port=int(plan["port"]),
    )


def list_plans() -> List[PlanInfo]:
    """
    List all available plans.

    Returns:
        List of PlanInfo objects for all available plans
    """
    return [get_plan_info(name) for name in sorted(PLANS.keys())]


def format_capacity(bytes_val: int) -> str:
    """
    Format capacity in human-readable form.

    Args:
        bytes_val: Capacity in bytes

    Returns:
        Formatted string (e.g., "100 GB", "1 TB")
    """
    if bytes_val >= 1099511627776:  # 1 TB
        return f"{bytes_val / 1099511627776:.1f} TB"
    elif bytes_val >= 1073741824:  # 1 GB
        return f"{bytes_val / 1073741824:.1f} GB"
    elif bytes_val >= 1048576:  # 1 MB
        return f"{bytes_val / 1048576:.1f} MB"
    else:
        return f"{bytes_val} bytes"


__all__ = [
    "PLANS",
    "PlanInfo",
    "PlanError",
    "PlanNotFoundError",
    "ProviderPlansDB",
    "apply_plan",
    "get_plan_info",
    "list_plans",
    "format_capacity",
]
