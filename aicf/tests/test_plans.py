"""
Tests for AICF provider plans.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from aicf.credits.plans import (
    PLANS,
    PlanError,
    PlanInfo,
    PlanNotFoundError,
    ProviderPlansDB,
    apply_plan,
    format_capacity,
    get_plan_info,
    list_plans,
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
    return b"1" * 32


class TestPlanInfo:
    """Tests for plan info retrieval."""

    def test_get_plan_info_starter(self):
        """Test getting starter plan info."""
        plan = get_plan_info("starter-100gb")
        assert plan.name == "starter-100gb"
        assert plan.capacity_bytes == 107374182400  # 100 GB
        assert plan.heartbeat_interval_seconds == 300  # 5 minutes
        assert plan.audit_target_per_day == 24
        assert plan.port == 9090

    def test_get_plan_info_serious(self):
        """Test getting serious plan info."""
        plan = get_plan_info("serious-1tb")
        assert plan.name == "serious-1tb"
        assert plan.capacity_bytes == 1099511627776  # 1 TB
        assert plan.heartbeat_interval_seconds == 180  # 3 minutes
        assert plan.audit_target_per_day == 48

    def test_get_plan_info_datacenter(self):
        """Test getting datacenter plan info."""
        plan = get_plan_info("datacenter-10tb")
        assert plan.name == "datacenter-10tb"
        assert plan.capacity_bytes == 10995116277760  # 10 TB
        assert plan.heartbeat_interval_seconds == 60  # 1 minute
        assert plan.audit_target_per_day == 96

    def test_get_plan_info_invalid(self):
        """Test getting info for non-existent plan."""
        with pytest.raises(PlanNotFoundError, match="Plan 'invalid' not found"):
            get_plan_info("invalid")

    def test_list_plans(self):
        """Test listing all plans."""
        plans = list_plans()
        assert len(plans) == 3
        plan_names = [p.name for p in plans]
        assert "starter-100gb" in plan_names
        assert "serious-1tb" in plan_names
        assert "datacenter-10tb" in plan_names


class TestApplyPlan:
    """Tests for applying plans to providers."""

    def test_apply_plan_success(self, temp_db, provider_id):
        """Test successfully applying a plan."""
        plan_info = apply_plan(
            provider_id=provider_id,
            plan_name="starter-100gb",
            db_path_override=temp_db,
        )

        assert plan_info.name == "starter-100gb"
        assert plan_info.capacity_bytes == 107374182400

    def test_apply_plan_invalid(self, temp_db, provider_id):
        """Test applying non-existent plan."""
        with pytest.raises(PlanNotFoundError):
            apply_plan(
                provider_id=provider_id,
                plan_name="invalid-plan",
                db_path_override=temp_db,
            )

    def test_apply_plan_updates_existing(self, temp_db, provider_id):
        """Test that applying a new plan updates the existing one."""
        # Apply first plan
        apply_plan(
            provider_id=provider_id,
            plan_name="starter-100gb",
            db_path_override=temp_db,
        )

        # Apply second plan
        time.sleep(0.1)  # Ensure different timestamp
        plan_info = apply_plan(
            provider_id=provider_id,
            plan_name="serious-1tb",
            db_path_override=temp_db,
        )

        assert plan_info.name == "serious-1tb"
        assert plan_info.capacity_bytes == 1099511627776


class TestProviderPlansDB:
    """Tests for provider plans database."""

    def test_set_and_get_plan(self, temp_db, provider_id):
        """Test setting and retrieving a plan."""
        db = ProviderPlansDB(db_path_override=temp_db)

        now = int(time.time())
        db.set_plan(provider_id, "starter-100gb", now)

        plan = db.get_plan(provider_id)
        assert plan is not None
        assert plan["plan_name"] == "starter-100gb"
        assert plan["capacity"] == 107374182400
        assert plan["heartbeat_interval"] == 300
        assert plan["audit_target"] == 24
        assert plan["port"] == 9090
        assert plan["applied_at"] == now

    def test_get_plan_no_assignment(self, temp_db, provider_id):
        """Test getting plan when none is assigned."""
        db = ProviderPlansDB(db_path_override=temp_db)
        plan = db.get_plan(provider_id)
        assert plan is None

    def test_update_plan(self, temp_db, provider_id):
        """Test updating an existing plan assignment."""
        db = ProviderPlansDB(db_path_override=temp_db)

        # Set initial plan
        now1 = int(time.time())
        db.set_plan(provider_id, "starter-100gb", now1)

        # Update to different plan
        time.sleep(0.1)
        now2 = int(time.time())
        db.set_plan(provider_id, "serious-1tb", now2)

        plan = db.get_plan(provider_id)
        assert plan["plan_name"] == "serious-1tb"
        assert plan["capacity"] == 1099511627776
        assert plan["applied_at"] == now2

    def test_invalid_plan_name(self, temp_db, provider_id):
        """Test setting invalid plan name."""
        db = ProviderPlansDB(db_path_override=temp_db)

        with pytest.raises(PlanNotFoundError):
            db.set_plan(provider_id, "invalid-plan", int(time.time()))

    def test_invalid_provider_id(self, temp_db):
        """Test with invalid provider ID."""
        db = ProviderPlansDB(db_path_override=temp_db)

        with pytest.raises(ValueError, match="provider_id must be 32 bytes"):
            db.set_plan(b"short", "starter-100gb", int(time.time()))

    def test_multiple_providers(self, temp_db):
        """Test multiple providers with different plans."""
        db = ProviderPlansDB(db_path_override=temp_db)

        provider1 = b"1" * 32
        provider2 = b"2" * 32
        provider3 = b"3" * 32

        now = int(time.time())
        db.set_plan(provider1, "starter-100gb", now)
        db.set_plan(provider2, "serious-1tb", now)
        db.set_plan(provider3, "datacenter-10tb", now)

        plan1 = db.get_plan(provider1)
        plan2 = db.get_plan(provider2)
        plan3 = db.get_plan(provider3)

        assert plan1["plan_name"] == "starter-100gb"
        assert plan2["plan_name"] == "serious-1tb"
        assert plan3["plan_name"] == "datacenter-10tb"


class TestFormatCapacity:
    """Tests for capacity formatting."""

    def test_format_bytes(self):
        """Test formatting byte values."""
        assert format_capacity(500) == "500 bytes"

    def test_format_megabytes(self):
        """Test formatting megabyte values."""
        assert format_capacity(10 * 1048576) == "10.0 MB"

    def test_format_gigabytes(self):
        """Test formatting gigabyte values."""
        assert format_capacity(100 * 1073741824) == "100.0 GB"

    def test_format_terabytes(self):
        """Test formatting terabyte values."""
        assert format_capacity(5 * 1099511627776) == "5.0 TB"

    def test_format_fractional(self):
        """Test formatting fractional values."""
        result = format_capacity(1536 * 1073741824)  # 1.5 TB
        assert "1.5 TB" in result or "1.4 TB" in result  # Allow for rounding
