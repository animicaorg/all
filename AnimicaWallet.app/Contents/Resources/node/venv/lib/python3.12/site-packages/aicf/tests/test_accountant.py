"""
Tests for AICF job accountant.
"""

import tempfile
import time
from pathlib import Path

import pytest

from aicf.accountant import (
    FileBackedJobAccountant,
    JobAccountant,
    JobAccountingRecord,
)


def test_job_accounting_record():
    """Test JobAccountingRecord creation."""
    record = JobAccountingRecord(
        job_id="job123",
        submitter="0xABC",
        plan="free",
        resource_units=100,
        status="queued",
        charge=0.0,
        timestamp=time.time(),
    )
    
    assert record.job_id == "job123"
    assert record.submitter == "0xABC"
    assert record.plan == "free"
    assert record.resource_units == 100
    assert record.status == "queued"
    assert record.charge == 0.0


def test_job_accountant_free_mode():
    """Test JobAccountant in free mode."""
    accountant = JobAccountant(
        rate_per_unit=0.0,
        free_units=1000,
        mode="free",
    )
    
    # Record job within free tier
    record = accountant.record_job(
        job_id="job1",
        submitter="user1",
        plan="free",
        resource_units=100,
    )
    assert record.charge == 0.0
    
    # Record another job within free tier
    record = accountant.record_job(
        job_id="job2",
        submitter="user1",
        plan="free",
        resource_units=200,
    )
    assert record.charge == 0.0
    
    # Try to exceed free tier
    with pytest.raises(ValueError, match="exceed free tier quota"):
        accountant.record_job(
            job_id="job3",
            submitter="user1",
            plan="free",
            resource_units=800,  # Would total 1100, exceeds 1000
        )


def test_job_accountant_paid_mode():
    """Test JobAccountant in paid mode."""
    accountant = JobAccountant(
        rate_per_unit=0.5,
        free_units=1000,
        mode="paid",
    )
    
    # First job within free tier
    record = accountant.record_job(
        job_id="job1",
        submitter="user1",
        plan="pro",
        resource_units=500,
    )
    assert record.charge == 0.0  # All covered by free tier
    
    # Second job partially covered by free tier
    record = accountant.record_job(
        job_id="job2",
        submitter="user1",
        plan="pro",
        resource_units=600,
    )
    # Used 500 so far, 500 free remaining, 100 billable
    assert record.charge == 50.0  # 100 * 0.5
    
    # Third job fully billable
    record = accountant.record_job(
        job_id="job3",
        submitter="user1",
        plan="pro",
        resource_units=100,
    )
    assert record.charge == 50.0  # 100 * 0.5


def test_job_accountant_update_status():
    """Test JobAccountant update_job_status."""
    accountant = JobAccountant(mode="free")
    
    record = accountant.record_job(
        job_id="job1",
        submitter="user1",
        plan="free",
        resource_units=100,
        status="queued",
    )
    assert record.status == "queued"
    
    accountant.update_job_status("job1", "running")
    record = accountant.get_record("job1")
    assert record.status == "running"
    
    accountant.update_job_status("job1", "completed")
    record = accountant.get_record("job1")
    assert record.status == "completed"


def test_job_accountant_get_record():
    """Test JobAccountant get_record."""
    accountant = JobAccountant(mode="free")
    
    accountant.record_job(
        job_id="job1",
        submitter="user1",
        plan="free",
        resource_units=100,
    )
    
    record = accountant.get_record("job1")
    assert record is not None
    assert record.job_id == "job1"
    
    # Non-existent job
    record = accountant.get_record("nonexistent")
    assert record is None


def test_job_accountant_get_submitter_usage():
    """Test JobAccountant get_submitter_usage."""
    accountant = JobAccountant(
        rate_per_unit=0.5,
        free_units=1000,
        mode="paid",
    )
    
    accountant.record_job(
        job_id="job1",
        submitter="user1",
        plan="pro",
        resource_units=500,
    )
    accountant.record_job(
        job_id="job2",
        submitter="user1",
        plan="pro",
        resource_units=600,
    )
    
    units, charge = accountant.get_submitter_usage("user1")
    assert units == 1100
    assert charge == 50.0  # Only 100 units billable (100 * 0.5)


def test_job_accountant_reset_submitter_usage():
    """Test JobAccountant reset_submitter_usage."""
    accountant = JobAccountant(mode="free", free_units=1000)
    
    accountant.record_job(
        job_id="job1",
        submitter="user1",
        plan="free",
        resource_units=900,
    )
    
    units, _ = accountant.get_submitter_usage("user1")
    assert units == 900
    
    # Reset usage
    accountant.reset_submitter_usage("user1")
    
    units, _ = accountant.get_submitter_usage("user1")
    assert units == 0


def test_job_accountant_get_all_records():
    """Test JobAccountant get_all_records."""
    accountant = JobAccountant(mode="free")
    
    accountant.record_job("job1", "user1", "free", 100)
    accountant.record_job("job2", "user2", "free", 200)
    
    records = accountant.get_all_records()
    assert len(records) == 2
    assert "job1" in records
    assert "job2" in records


def test_job_accountant_clear():
    """Test JobAccountant clear."""
    accountant = JobAccountant(mode="free")
    
    accountant.record_job("job1", "user1", "free", 100)
    accountant.record_job("job2", "user2", "free", 200)
    
    accountant.clear()
    
    records = accountant.get_all_records()
    assert len(records) == 0


def test_file_backed_job_accountant_persistence():
    """Test FileBackedJobAccountant saves and loads data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "accountant.json"
        
        # Create accountant and record jobs
        accountant = FileBackedJobAccountant(
            file_path,
            rate_per_unit=0.5,
            free_units=1000,
            mode="paid",
            auto_save_interval=0.1,
        )
        accountant.record_job("job1", "user1", "pro", 500)
        accountant.save()
        
        # Create new accountant from same file
        accountant2 = FileBackedJobAccountant(file_path)
        record = accountant2.get_record("job1")
        assert record is not None
        assert record.job_id == "job1"
        assert record.resource_units == 500


def test_file_backed_job_accountant_auto_save():
    """Test FileBackedJobAccountant auto-save."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "accountant.json"
        
        # Create accountant with short auto-save interval
        accountant = FileBackedJobAccountant(
            file_path,
            auto_save_interval=0.1,
        )
        accountant.record_job("job1", "user1", "free", 100)
        
        # Wait for auto-save
        time.sleep(0.2)
        
        # Create new accountant and verify data was saved
        accountant2 = FileBackedJobAccountant(file_path)
        record = accountant2.get_record("job1")
        assert record is not None
        assert record.job_id == "job1"
