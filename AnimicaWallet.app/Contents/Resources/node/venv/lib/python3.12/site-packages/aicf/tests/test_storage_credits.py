"""
Tests for AICF storage credits calculation and storage.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from aicf.credits.storage import (
    AUDIT_PASS_BONUS,
    BANDWIDTH_RATE_PER_GB,
    STORAGE_RATE_PER_GB_MONTH,
    StorageCreditRecord,
    StorageCreditsDB,
    calculate_storage_credits,
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
def provider_id():
    """Sample provider ID."""
    return b"0" * 32


class TestCalculateStorageCredits:
    """Tests for storage credit calculation."""

    def test_basic_calculation(self, provider_id):
        """Test basic credit calculation."""
        credits = calculate_storage_credits(
            provider_id=provider_id,
            gb_stored=100.0,
            audit_pass_count=10,
            audit_fail_count=0,
            bandwidth_gb=50.0,
            reliability_score=10000,  # 100%
        )

        # 100 GB * 100 + 10 audits * 10 + 50 GB * 5 = 10000 + 100 + 250 = 10350
        assert credits == 10350

    def test_reliability_multiplier(self, provider_id):
        """Test reliability score multiplier."""
        # 50% reliability
        credits = calculate_storage_credits(
            provider_id=provider_id,
            gb_stored=100.0,
            audit_pass_count=10,
            audit_fail_count=5,
            bandwidth_gb=50.0,
            reliability_score=5000,  # 50%
        )

        # (10000 + 100 + 250) * 0.5 = 5175
        assert credits == 5175

    def test_zero_values(self, provider_id):
        """Test with zero values."""
        credits = calculate_storage_credits(
            provider_id=provider_id,
            gb_stored=0.0,
            audit_pass_count=0,
            audit_fail_count=0,
            bandwidth_gb=0.0,
            reliability_score=10000,
        )
        assert credits == 0

    def test_high_reliability_bonus(self, provider_id):
        """Test that higher reliability increases credits."""
        credits_low = calculate_storage_credits(
            provider_id=provider_id,
            gb_stored=100.0,
            audit_pass_count=10,
            audit_fail_count=0,
            bandwidth_gb=50.0,
            reliability_score=3000,  # 30%
        )

        credits_high = calculate_storage_credits(
            provider_id=provider_id,
            gb_stored=100.0,
            audit_pass_count=10,
            audit_fail_count=0,
            bandwidth_gb=50.0,
            reliability_score=9000,  # 90%
        )

        assert credits_high > credits_low

    def test_invalid_provider_id(self):
        """Test with invalid provider ID length."""
        with pytest.raises(ValueError, match="provider_id must be 32 bytes"):
            calculate_storage_credits(
                provider_id=b"short",
                gb_stored=100.0,
                audit_pass_count=10,
                audit_fail_count=0,
                bandwidth_gb=50.0,
                reliability_score=10000,
            )

    def test_invalid_reliability_score(self, provider_id):
        """Test with invalid reliability score."""
        with pytest.raises(ValueError, match="reliability_score must be 0-10000"):
            calculate_storage_credits(
                provider_id=provider_id,
                gb_stored=100.0,
                audit_pass_count=10,
                audit_fail_count=0,
                bandwidth_gb=50.0,
                reliability_score=15000,
            )

    def test_negative_values(self, provider_id):
        """Test that negative values are rejected."""
        with pytest.raises(ValueError):
            calculate_storage_credits(
                provider_id=provider_id,
                gb_stored=-10.0,
                audit_pass_count=10,
                audit_fail_count=0,
                bandwidth_gb=50.0,
                reliability_score=10000,
            )


class TestStorageCreditsDB:
    """Tests for storage credits database."""

    def test_record_and_retrieve(self, temp_db, provider_id):
        """Test recording and retrieving credits."""
        db = StorageCreditsDB(db_path_override=temp_db)

        db.record_credits(
            provider_id=provider_id,
            period="2026-02",
            gb_stored=100.0,
            audits_passed=10,
            audits_failed=1,
            bandwidth_gb=50.0,
            reliability_score=9000,
            credits_earned=10000,
        )

        record = db.get_credits(provider_id, "2026-02")
        assert record is not None
        assert record.provider_id == provider_id
        assert record.period == "2026-02"
        assert record.gb_stored == 100.0
        assert record.audits_passed == 10
        assert record.audits_failed == 1
        assert record.bandwidth_gb == 50.0
        assert record.reliability_score == 9000
        assert record.credits_earned == 10000
        assert record.settled is False

    def test_update_existing(self, temp_db, provider_id):
        """Test updating existing record."""
        db = StorageCreditsDB(db_path_override=temp_db)

        # Initial record
        db.record_credits(
            provider_id=provider_id,
            period="2026-02",
            gb_stored=100.0,
            audits_passed=10,
            audits_failed=1,
            bandwidth_gb=50.0,
            reliability_score=9000,
            credits_earned=10000,
        )

        # Update
        db.record_credits(
            provider_id=provider_id,
            period="2026-02",
            gb_stored=150.0,
            audits_passed=15,
            audits_failed=2,
            bandwidth_gb=75.0,
            reliability_score=9500,
            credits_earned=15000,
        )

        record = db.get_credits(provider_id, "2026-02")
        assert record.gb_stored == 150.0
        assert record.credits_earned == 15000

    def test_list_provider_credits(self, temp_db, provider_id):
        """Test listing credits for a provider."""
        db = StorageCreditsDB(db_path_override=temp_db)

        # Add multiple periods
        for i, period in enumerate(["2026-01", "2026-02", "2026-03"]):
            db.record_credits(
                provider_id=provider_id,
                period=period,
                gb_stored=100.0 * (i + 1),
                audits_passed=10,
                audits_failed=0,
                bandwidth_gb=50.0,
                reliability_score=9000,
                credits_earned=1000 * (i + 1),
            )

        records = db.list_provider_credits(provider_id)
        assert len(records) == 3
        # Should be sorted by period descending
        assert records[0].period == "2026-03"
        assert records[1].period == "2026-02"
        assert records[2].period == "2026-01"

    def test_mark_settled(self, temp_db, provider_id):
        """Test marking credits as settled."""
        db = StorageCreditsDB(db_path_override=temp_db)

        db.record_credits(
            provider_id=provider_id,
            period="2026-02",
            gb_stored=100.0,
            audits_passed=10,
            audits_failed=0,
            bandwidth_gb=50.0,
            reliability_score=9000,
            credits_earned=10000,
        )

        # Mark settled
        db.mark_settled(provider_id, "2026-02")

        record = db.get_credits(provider_id, "2026-02")
        assert record.settled is True
        assert record.settled_at is not None

    def test_get_total_claimable(self, temp_db, provider_id):
        """Test getting total claimable credits."""
        db = StorageCreditsDB(db_path_override=temp_db)

        # Add settled and unsettled credits
        db.record_credits(
            provider_id=provider_id,
            period="2026-01",
            gb_stored=100.0,
            audits_passed=10,
            audits_failed=0,
            bandwidth_gb=50.0,
            reliability_score=9000,
            credits_earned=5000,
        )
        db.mark_settled(provider_id, "2026-01")

        db.record_credits(
            provider_id=provider_id,
            period="2026-02",
            gb_stored=100.0,
            audits_passed=10,
            audits_failed=0,
            bandwidth_gb=50.0,
            reliability_score=9000,
            credits_earned=6000,
        )

        db.record_credits(
            provider_id=provider_id,
            period="2026-03",
            gb_stored=100.0,
            audits_passed=10,
            audits_failed=0,
            bandwidth_gb=50.0,
            reliability_score=9000,
            credits_earned=7000,
        )

        total = db.get_total_claimable(provider_id)
        assert total == 13000  # Only unsettled (6000 + 7000)

    def test_filter_by_settled_status(self, temp_db, provider_id):
        """Test filtering by settlement status."""
        db = StorageCreditsDB(db_path_override=temp_db)

        # Add multiple periods
        db.record_credits(
            provider_id=provider_id,
            period="2026-01",
            gb_stored=100.0,
            audits_passed=10,
            audits_failed=0,
            bandwidth_gb=50.0,
            reliability_score=9000,
            credits_earned=5000,
        )
        db.mark_settled(provider_id, "2026-01")

        db.record_credits(
            provider_id=provider_id,
            period="2026-02",
            gb_stored=100.0,
            audits_passed=10,
            audits_failed=0,
            bandwidth_gb=50.0,
            reliability_score=9000,
            credits_earned=6000,
        )

        # Get only unsettled
        unsettled = db.list_provider_credits(provider_id, settled=False)
        assert len(unsettled) == 1
        assert unsettled[0].period == "2026-02"

        # Get only settled
        settled = db.list_provider_credits(provider_id, settled=True)
        assert len(settled) == 1
        assert settled[0].period == "2026-01"
