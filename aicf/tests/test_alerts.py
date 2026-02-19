"""
Tests for AICF provider alerts.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from aicf.credits.alerts import (
    Alert,
    AlertType,
    AlertsDB,
    check_alerts,
    clear_alert,
    get_active_alerts,
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
    return b"2" * 32


class TestAlertsDB:
    """Tests for alerts database."""

    def test_create_alert(self, temp_db, provider_id):
        """Test creating an alert."""
        db = AlertsDB(db_path_override=temp_db)

        alert_id = db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.LOW_DISK_FREE,
            severity="warning",
            message="Low disk space: 8.5% free",
            metadata='{"disk_free_pct": 8.5}',
        )

        assert alert_id > 0

    def test_get_active_alerts(self, temp_db, provider_id):
        """Test retrieving active alerts."""
        db = AlertsDB(db_path_override=temp_db)

        # Create multiple alerts
        db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.LOW_DISK_FREE,
            severity="warning",
            message="Low disk space",
        )

        db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.FAILED_AUDITS,
            severity="critical",
            message="High failure rate",
        )

        alerts = db.get_active_alerts(provider_id)
        assert len(alerts) == 2

    def test_filter_by_alert_type(self, temp_db, provider_id):
        """Test filtering alerts by type."""
        db = AlertsDB(db_path_override=temp_db)

        db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.LOW_DISK_FREE,
            severity="warning",
            message="Low disk space",
        )

        db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.FAILED_AUDITS,
            severity="critical",
            message="High failure rate",
        )

        disk_alerts = db.get_active_alerts(
            provider_id, alert_type=AlertType.LOW_DISK_FREE
        )
        assert len(disk_alerts) == 1
        assert disk_alerts[0].alert_type == AlertType.LOW_DISK_FREE

    def test_clear_alert(self, temp_db, provider_id):
        """Test clearing alerts."""
        db = AlertsDB(db_path_override=temp_db)

        # Create alert
        db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.LOW_DISK_FREE,
            severity="warning",
            message="Low disk space",
        )

        # Verify it's active
        alerts = db.get_active_alerts(provider_id)
        assert len(alerts) == 1

        # Clear it
        count = db.clear_alert(provider_id, AlertType.LOW_DISK_FREE)
        assert count == 1

        # Verify it's cleared
        alerts = db.get_active_alerts(provider_id)
        assert len(alerts) == 0

    def test_clear_all_alerts(self, temp_db, provider_id):
        """Test clearing all alerts for a provider."""
        db = AlertsDB(db_path_override=temp_db)

        # Create multiple alerts
        db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.LOW_DISK_FREE,
            severity="warning",
            message="Low disk space",
        )

        db.create_alert(
            provider_id=provider_id,
            alert_type=AlertType.FAILED_AUDITS,
            severity="critical",
            message="High failure rate",
        )

        # Clear all
        count = db.clear_all_alerts(provider_id)
        assert count == 2

        # Verify all cleared
        alerts = db.get_active_alerts(provider_id)
        assert len(alerts) == 0

    def test_alert_severity_validation(self, temp_db, provider_id):
        """Test that invalid severity is rejected."""
        db = AlertsDB(db_path_override=temp_db)

        with pytest.raises(ValueError, match="severity must be"):
            db.create_alert(
                provider_id=provider_id,
                alert_type=AlertType.LOW_DISK_FREE,
                severity="invalid",
                message="Test",
            )

    def test_invalid_provider_id(self, temp_db):
        """Test with invalid provider ID."""
        db = AlertsDB(db_path_override=temp_db)

        with pytest.raises(ValueError, match="provider_id must be 32 bytes"):
            db.create_alert(
                provider_id=b"short",
                alert_type=AlertType.LOW_DISK_FREE,
                severity="warning",
                message="Test",
            )


class TestCheckAlerts:
    """Tests for check_alerts function."""

    def test_low_disk_free_warning(self, temp_db, provider_id):
        """Test LOW_DISK_FREE warning trigger."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=8.5,
            failed_audits_last_hour=0,
            reliability_score=9000,
            unsynced_blob_count=0,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 1
        assert alerts[0].alert_type == AlertType.LOW_DISK_FREE
        assert alerts[0].severity == "warning"

    def test_low_disk_free_critical(self, temp_db, provider_id):
        """Test LOW_DISK_FREE critical trigger."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=3.0,
            failed_audits_last_hour=0,
            reliability_score=9000,
            unsynced_blob_count=0,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_failed_audits_warning(self, temp_db, provider_id):
        """Test FAILED_AUDITS warning trigger."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=50.0,
            failed_audits_last_hour=8,
            reliability_score=9000,
            unsynced_blob_count=0,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 1
        assert alerts[0].alert_type == AlertType.FAILED_AUDITS
        assert alerts[0].severity == "warning"

    def test_failed_audits_critical(self, temp_db, provider_id):
        """Test FAILED_AUDITS critical trigger."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=50.0,
            failed_audits_last_hour=15,
            reliability_score=9000,
            unsynced_blob_count=0,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_low_reliability_warning(self, temp_db, provider_id):
        """Test LOW_RELIABILITY warning trigger."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=50.0,
            failed_audits_last_hour=0,
            reliability_score=2500,
            unsynced_blob_count=0,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 1
        assert alerts[0].alert_type == AlertType.LOW_RELIABILITY
        assert alerts[0].severity == "warning"

    def test_low_reliability_critical(self, temp_db, provider_id):
        """Test LOW_RELIABILITY critical trigger."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=50.0,
            failed_audits_last_hour=0,
            reliability_score=500,
            unsynced_blob_count=0,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_sync_backlog_warning(self, temp_db, provider_id):
        """Test SYNC_BACKLOG warning trigger."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=50.0,
            failed_audits_last_hour=0,
            reliability_score=9000,
            unsynced_blob_count=250,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 1
        assert alerts[0].alert_type == AlertType.SYNC_BACKLOG
        assert alerts[0].severity == "warning"

    def test_sync_backlog_critical(self, temp_db, provider_id):
        """Test SYNC_BACKLOG critical trigger."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=50.0,
            failed_audits_last_hour=0,
            reliability_score=9000,
            unsynced_blob_count=600,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_high_egress(self, temp_db, provider_id):
        """Test HIGH_EGRESS trigger."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=50.0,
            failed_audits_last_hour=0,
            reliability_score=9000,
            unsynced_blob_count=0,
            egress_gb=1500.0,
            plan_bandwidth_limit_gb=1000.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 1
        assert alerts[0].alert_type == AlertType.HIGH_EGRESS
        assert alerts[0].severity == "warning"

    def test_no_high_egress_without_limit(self, temp_db, provider_id):
        """Test that HIGH_EGRESS doesn't trigger without a limit."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=50.0,
            failed_audits_last_hour=0,
            reliability_score=9000,
            unsynced_blob_count=0,
            egress_gb=1500.0,
            plan_bandwidth_limit_gb=None,
            db_path_override=temp_db,
        )

        assert len(alerts) == 0

    def test_multiple_alerts(self, temp_db, provider_id):
        """Test multiple alerts triggered simultaneously."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=5.0,
            failed_audits_last_hour=12,
            reliability_score=2000,
            unsynced_blob_count=300,
            egress_gb=1500.0,
            plan_bandwidth_limit_gb=1000.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 5
        alert_types = {a.alert_type for a in alerts}
        assert AlertType.LOW_DISK_FREE in alert_types
        assert AlertType.FAILED_AUDITS in alert_types
        assert AlertType.LOW_RELIABILITY in alert_types
        assert AlertType.SYNC_BACKLOG in alert_types
        assert AlertType.HIGH_EGRESS in alert_types

    def test_no_alerts(self, temp_db, provider_id):
        """Test when no alerts should be triggered."""
        alerts = check_alerts(
            provider_id=provider_id,
            disk_free_pct=50.0,
            failed_audits_last_hour=2,
            reliability_score=9000,
            unsynced_blob_count=50,
            egress_gb=500.0,
            plan_bandwidth_limit_gb=1000.0,
            db_path_override=temp_db,
        )

        assert len(alerts) == 0


class TestAlertHelpers:
    """Tests for alert helper functions."""

    def test_get_active_alerts(self, temp_db, provider_id):
        """Test get_active_alerts helper."""
        # Create some alerts
        check_alerts(
            provider_id=provider_id,
            disk_free_pct=5.0,
            failed_audits_last_hour=0,
            reliability_score=9000,
            unsynced_blob_count=0,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        alerts = get_active_alerts(provider_id, db_path_override=temp_db)
        assert len(alerts) == 1

    def test_clear_alert_helper(self, temp_db, provider_id):
        """Test clear_alert helper."""
        # Create alert
        check_alerts(
            provider_id=provider_id,
            disk_free_pct=5.0,
            failed_audits_last_hour=0,
            reliability_score=9000,
            unsynced_blob_count=0,
            egress_gb=0.0,
            db_path_override=temp_db,
        )

        # Clear it
        count = clear_alert(
            provider_id, AlertType.LOW_DISK_FREE, db_path_override=temp_db
        )
        assert count == 1

        # Verify cleared
        alerts = get_active_alerts(provider_id, db_path_override=temp_db)
        assert len(alerts) == 0
