"""
Tests for AICF storage credit settlement.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from aicf.credits.settlement import (
    ProviderMetrics,
    SettlementError,
    claim_storage_credits,
    get_claimable_summary,
    settle_period,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield Path(path)
    # Cleanup
    try:
        os.unlink(path)
    except Exception:
        pass


@pytest.fixture
def provider_metrics():
    """Sample provider metrics for testing."""
    return [
        ProviderMetrics(
            provider_id=b"1" * 32,
            gb_stored=100.0,
            audits_passed=20,
            audits_failed=1,
            bandwidth_gb=50.0,
            reliability_score=9000,
        ),
        ProviderMetrics(
            provider_id=b"2" * 32,
            gb_stored=500.0,
            audits_passed=40,
            audits_failed=2,
            bandwidth_gb=200.0,
            reliability_score=9500,
        ),
    ]


class TestSettlePeriod:
    """Tests for settle_period function."""

    def test_settle_single_provider(self, temp_db):
        """Test settling credits for a single provider."""
        metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=100.0,
                audits_passed=10,
                audits_failed=0,
                bandwidth_gb=50.0,
                reliability_score=10000,  # 100%
            )
        ]

        results = settle_period("2026-02", metrics, db_path_override=str(temp_db))

        assert len(results) == 1
        assert b"1" * 32 in results
        # 100*100 + 10*10 + 50*5 = 10000 + 100 + 250 = 10350
        assert results[b"1" * 32] == 10350

    def test_settle_multiple_providers(self, temp_db, provider_metrics):
        """Test settling credits for multiple providers."""
        results = settle_period(
            "2026-02", provider_metrics, db_path_override=str(temp_db)
        )

        assert len(results) == 2
        assert b"1" * 32 in results
        assert b"2" * 32 in results

    def test_settle_invalid_period_format(self, temp_db, provider_metrics):
        """Test that invalid period format raises error."""
        with pytest.raises(SettlementError, match="Invalid period format"):
            settle_period("2026/02", provider_metrics, db_path_override=str(temp_db))

        with pytest.raises(SettlementError, match="Invalid period format"):
            settle_period("202602", provider_metrics, db_path_override=str(temp_db))

        with pytest.raises(SettlementError, match="Invalid period format"):
            settle_period("2026-13", provider_metrics, db_path_override=str(temp_db))

    def test_settle_updates_database(self, temp_db):
        """Test that settlement updates the database correctly."""
        from aicf.credits.storage import StorageCreditsDB

        metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=100.0,
                audits_passed=10,
                audits_failed=1,
                bandwidth_gb=50.0,
                reliability_score=9000,
            )
        ]

        settle_period("2026-02", metrics, db_path_override=str(temp_db))

        # Verify in database
        db = StorageCreditsDB(db_path_override=temp_db)
        record = db.get_credits(b"1" * 32, "2026-02")

        assert record is not None
        assert record.gb_stored == 100.0
        assert record.audits_passed == 10
        assert record.audits_failed == 1
        assert record.bandwidth_gb == 50.0
        assert record.reliability_score == 9000
        assert record.settled is False  # Not claimed yet

    def test_settle_same_period_twice(self, temp_db, provider_metrics):
        """Test settling the same period twice updates credits."""
        # First settlement
        results1 = settle_period(
            "2026-02", provider_metrics, db_path_override=str(temp_db)
        )

        # Update metrics
        updated_metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=150.0,  # Increased
                audits_passed=25,
                audits_failed=1,
                bandwidth_gb=75.0,
                reliability_score=9500,
            )
        ]

        # Second settlement
        results2 = settle_period(
            "2026-02", updated_metrics, db_path_override=str(temp_db)
        )

        # Should have updated credits
        assert results2[b"1" * 32] > results1[b"1" * 32]


class TestClaimStorageCredits:
    """Tests for claim_storage_credits function."""

    def test_claim_all_credits(self, temp_db):
        """Test claiming all available credits."""
        # Setup: settle some credits
        metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=100.0,
                audits_passed=10,
                audits_failed=0,
                bandwidth_gb=50.0,
                reliability_score=10000,
            )
        ]
        settle_period("2026-01", metrics, db_path_override=str(temp_db))
        settle_period("2026-02", metrics, db_path_override=str(temp_db))

        # Claim all
        total, periods = claim_storage_credits(
            b"1" * 32, amount=None, db_path_override=str(temp_db)
        )

        assert total == 20700  # 10350 * 2
        assert len(periods) == 2
        assert "2026-01" in periods
        assert "2026-02" in periods

    def test_claim_partial_credits(self, temp_db):
        """Test claiming partial credits."""
        # Setup: settle credits for multiple periods
        metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=100.0,
                audits_passed=10,
                audits_failed=0,
                bandwidth_gb=50.0,
                reliability_score=10000,
            )
        ]
        settle_period("2026-01", metrics, db_path_override=str(temp_db))
        settle_period("2026-02", metrics, db_path_override=str(temp_db))
        settle_period("2026-03", metrics, db_path_override=str(temp_db))

        # Claim only enough for one period
        total, periods = claim_storage_credits(
            b"1" * 32, amount=10350, db_path_override=str(temp_db)
        )

        assert total == 10350
        assert len(periods) == 1
        assert "2026-01" in periods  # Should claim oldest first

    def test_claim_no_credits_available(self, temp_db):
        """Test claiming when no credits are available."""
        total, periods = claim_storage_credits(
            b"1" * 32, amount=None, db_path_override=str(temp_db)
        )

        assert total == 0
        assert len(periods) == 0

    def test_claim_marks_as_settled(self, temp_db):
        """Test that claiming marks periods as settled."""
        from aicf.credits.storage import StorageCreditsDB

        # Setup
        metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=100.0,
                audits_passed=10,
                audits_failed=0,
                bandwidth_gb=50.0,
                reliability_score=10000,
            )
        ]
        settle_period("2026-02", metrics, db_path_override=str(temp_db))

        # Claim
        claim_storage_credits(b"1" * 32, amount=None, db_path_override=str(temp_db))

        # Verify marked as settled
        db = StorageCreditsDB(db_path_override=temp_db)
        record = db.get_credits(b"1" * 32, "2026-02")

        assert record.settled is True
        assert record.settled_at is not None

    def test_claim_already_settled_not_reclaimed(self, temp_db):
        """Test that already settled credits are not claimed again."""
        # Setup
        metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=100.0,
                audits_passed=10,
                audits_failed=0,
                bandwidth_gb=50.0,
                reliability_score=10000,
            )
        ]
        settle_period("2026-02", metrics, db_path_override=str(temp_db))

        # First claim
        total1, _ = claim_storage_credits(
            b"1" * 32, amount=None, db_path_override=str(temp_db)
        )

        # Second claim
        total2, periods2 = claim_storage_credits(
            b"1" * 32, amount=None, db_path_override=str(temp_db)
        )

        assert total1 > 0
        assert total2 == 0
        assert len(periods2) == 0

    def test_claim_invalid_provider_id(self, temp_db):
        """Test claiming with invalid provider ID."""
        with pytest.raises(ValueError, match="provider_id must be 32 bytes"):
            claim_storage_credits(b"short", amount=None, db_path_override=str(temp_db))


class TestGetClaimableSummary:
    """Tests for get_claimable_summary function."""

    def test_summary_with_credits(self, temp_db):
        """Test getting summary when credits are available."""
        # Setup
        metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=100.0,
                audits_passed=10,
                audits_failed=0,
                bandwidth_gb=50.0,
                reliability_score=10000,
            )
        ]
        settle_period("2026-01", metrics, db_path_override=str(temp_db))
        settle_period("2026-02", metrics, db_path_override=str(temp_db))

        summary = get_claimable_summary(b"1" * 32, db_path_override=str(temp_db))

        assert summary["total_claimable"] == 20700
        assert summary["period_count"] == 2
        assert len(summary["periods"]) == 2

        # Check period details
        period_map = {p["period"]: p for p in summary["periods"]}
        assert "2026-01" in period_map
        assert "2026-02" in period_map
        assert period_map["2026-01"]["credits"] == 10350
        assert period_map["2026-01"]["gb_stored"] == 100.0

    def test_summary_no_credits(self, temp_db):
        """Test getting summary when no credits are available."""
        summary = get_claimable_summary(b"1" * 32, db_path_override=str(temp_db))

        assert summary["total_claimable"] == 0
        assert summary["period_count"] == 0
        assert len(summary["periods"]) == 0

    def test_summary_excludes_settled(self, temp_db):
        """Test that summary excludes already settled credits."""
        # Setup
        metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=100.0,
                audits_passed=10,
                audits_failed=0,
                bandwidth_gb=50.0,
                reliability_score=10000,
            )
        ]
        settle_period("2026-01", metrics, db_path_override=str(temp_db))
        settle_period("2026-02", metrics, db_path_override=str(temp_db))

        # Claim one period
        claim_storage_credits(
            b"1" * 32, amount=10350, db_path_override=str(temp_db)
        )

        # Get summary
        summary = get_claimable_summary(b"1" * 32, db_path_override=str(temp_db))

        assert summary["total_claimable"] == 10350  # Only one period left
        assert summary["period_count"] == 1
        assert summary["periods"][0]["period"] == "2026-02"

    def test_summary_sorted_descending(self, temp_db):
        """Test that periods are sorted in descending order."""
        # Setup
        metrics = [
            ProviderMetrics(
                provider_id=b"1" * 32,
                gb_stored=100.0,
                audits_passed=10,
                audits_failed=0,
                bandwidth_gb=50.0,
                reliability_score=10000,
            )
        ]
        settle_period("2026-01", metrics, db_path_override=str(temp_db))
        settle_period("2026-02", metrics, db_path_override=str(temp_db))
        settle_period("2026-03", metrics, db_path_override=str(temp_db))

        summary = get_claimable_summary(b"1" * 32, db_path_override=str(temp_db))

        periods = [p["period"] for p in summary["periods"]]
        assert periods == ["2026-03", "2026-02", "2026-01"]
