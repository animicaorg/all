"""
AICF Storage Credit Settlement Module

This module implements monthly settlement of storage credits for providers.
It queries audit results, calculates storage and bandwidth, computes credits,
and integrates with the AICF treasury for claiming.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from aicf.credits.storage import (
    StorageCreditsDB,
    calculate_storage_credits,
)


@dataclass
class ProviderMetrics:
    """Metrics for a provider during a settlement period."""

    provider_id: bytes
    gb_stored: float  # Average GB stored
    audits_passed: int
    audits_failed: int
    bandwidth_gb: float
    reliability_score: int


class SettlementError(Exception):
    """Base error for settlement operations."""


def settle_period(
    period: str,
    provider_metrics: List[ProviderMetrics],
    db_path_override: Optional[str] = None,
) -> Dict[bytes, int]:
    """
    Settle credits for all providers for a given period.

    For each provider:
    1. Query audit results for period (via provider_metrics)
    2. Calculate storage (from assignments, via provider_metrics)
    3. Calculate bandwidth (from service logs, via provider_metrics)
    4. Compute credits using calculate_storage_credits()
    5. Record in credits table
    6. Make claimable via AICF treasury (future: integrate with treasury.state)

    Args:
        period: Period in YYYY-MM format (e.g., "2026-02")
        provider_metrics: List of provider metrics for the period
        db_path_override: Optional database path override

    Returns:
        Dictionary mapping provider_id to credits_earned

    Raises:
        SettlementError: If period format is invalid or settlement fails
    """
    # Validate period format
    if not _validate_period_format(period):
        raise SettlementError(f"Invalid period format: {period}. Expected YYYY-MM")

    db = StorageCreditsDB(db_path_override=db_path_override)
    results = {}

    for metrics in provider_metrics:
        # Calculate credits
        credits = calculate_storage_credits(
            provider_id=metrics.provider_id,
            gb_stored=metrics.gb_stored,
            audit_pass_count=metrics.audits_passed,
            audit_fail_count=metrics.audits_failed,
            bandwidth_gb=metrics.bandwidth_gb,
            reliability_score=metrics.reliability_score,
        )

        # Record in database
        db.record_credits(
            provider_id=metrics.provider_id,
            period=period,
            gb_stored=metrics.gb_stored,
            audits_passed=metrics.audits_passed,
            audits_failed=metrics.audits_failed,
            bandwidth_gb=metrics.bandwidth_gb,
            reliability_score=metrics.reliability_score,
            credits_earned=credits,
        )

        results[metrics.provider_id] = credits

    return results


def claim_storage_credits(
    provider_id: bytes,
    amount: Optional[int] = None,
    db_path_override: Optional[str] = None,
) -> Tuple[int, List[str]]:
    """
    Claim storage credits for a provider.

    This marks periods as settled and prepares them for withdrawal
    from the AICF treasury.

    Args:
        provider_id: Provider identifier (32 bytes)
        amount: Optional specific amount to claim. If None, claims all available.
        db_path_override: Optional database path override

    Returns:
        Tuple of (total_claimed, list_of_settled_periods)

    Raises:
        SettlementError: If claiming fails
    """
    if len(provider_id) != 32:
        raise ValueError("provider_id must be 32 bytes")

    db = StorageCreditsDB(db_path_override=db_path_override)

    # Get unsettled credits
    unsettled = db.list_provider_credits(provider_id, settled=False)

    if not unsettled:
        return (0, [])

    # Sort by period (oldest first)
    unsettled.sort(key=lambda x: x.period)

    claimed = 0
    settled_periods = []

    for record in unsettled:
        if amount is not None and claimed >= amount:
            break

        # Mark as settled
        db.mark_settled(provider_id, record.period)
        claimed += record.credits_earned
        settled_periods.append(record.period)

    return (claimed, settled_periods)


def get_claimable_summary(
    provider_id: bytes, db_path_override: Optional[str] = None
) -> Dict[str, object]:
    """
    Get summary of claimable credits for a provider.

    Args:
        provider_id: Provider identifier
        db_path_override: Optional database path override

    Returns:
        Dictionary with claimable summary:
        - total_claimable: Total credits available to claim
        - period_count: Number of unsettled periods
        - periods: List of period details
    """
    db = StorageCreditsDB(db_path_override=db_path_override)

    unsettled = db.list_provider_credits(provider_id, settled=False)
    total = sum(r.credits_earned for r in unsettled)

    periods = [
        {
            "period": r.period,
            "credits": r.credits_earned,
            "gb_stored": r.gb_stored,
            "audits_passed": r.audits_passed,
            "bandwidth_gb": r.bandwidth_gb,
        }
        for r in sorted(unsettled, key=lambda x: x.period, reverse=True)
    ]

    return {
        "total_claimable": total,
        "period_count": len(unsettled),
        "periods": periods,
    }


def _validate_period_format(period: str) -> bool:
    """
    Validate period format is YYYY-MM.

    Args:
        period: Period string to validate

    Returns:
        True if valid, False otherwise
    """
    if len(period) != 7:
        return False
    if period[4] != "-":
        return False

    try:
        year = int(period[:4])
        month = int(period[5:7])
        return 1 <= month <= 12 and year >= 2000
    except ValueError:
        return False


__all__ = [
    "ProviderMetrics",
    "SettlementError",
    "settle_period",
    "claim_storage_credits",
    "get_claimable_summary",
]
