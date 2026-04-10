"""
Tests for billing usage store.
"""

import tempfile
import time
from pathlib import Path

import pytest

from billing.store import FileBackedUsageStore, UsageRecord, UsageStore


def test_usage_record():
    """Test UsageRecord creation."""
    record = UsageRecord(
        api_key="test_key",
        plan="free",
        requests_count=100,
        da_bytes_posted=1024,
        rpc_calls=50,
        aicf_units_used=10,
        last_request_time=time.time(),
        window_start=time.time(),
        window_requests=5,
    )
    
    assert record.api_key == "test_key"
    assert record.plan == "free"
    assert record.requests_count == 100
    assert record.da_bytes_posted == 1024
    assert record.rpc_calls == 50
    assert record.aicf_units_used == 10


def test_usage_store_get_record():
    """Test UsageStore get_record creates and retrieves records."""
    store = UsageStore()
    
    # Get non-existent record (should create)
    record = store.get_record("key1", "free")
    assert record.api_key == "key1"
    assert record.plan == "free"
    assert record.requests_count == 0
    
    # Get existing record
    record2 = store.get_record("key1")
    assert record2.api_key == "key1"


def test_usage_store_increment_requests():
    """Test UsageStore increment_requests."""
    store = UsageStore()
    
    # Increment requests
    store.increment_requests("key1", count=5)
    record = store.get_record("key1")
    assert record.requests_count == 5
    
    # Increment again
    store.increment_requests("key1", count=3)
    record = store.get_record("key1")
    assert record.requests_count == 8


def test_usage_store_increment_da_bytes():
    """Test UsageStore increment_da_bytes."""
    store = UsageStore()
    
    store.increment_da_bytes("key1", 1024)
    record = store.get_record("key1")
    assert record.da_bytes_posted == 1024
    
    store.increment_da_bytes("key1", 512)
    record = store.get_record("key1")
    assert record.da_bytes_posted == 1536


def test_usage_store_increment_rpc_calls():
    """Test UsageStore increment_rpc_calls."""
    store = UsageStore()
    
    store.increment_rpc_calls("key1", count=10)
    record = store.get_record("key1")
    assert record.rpc_calls == 10


def test_usage_store_increment_aicf_units():
    """Test UsageStore increment_aicf_units."""
    store = UsageStore()
    
    store.increment_aicf_units("key1", 100)
    record = store.get_record("key1")
    assert record.aicf_units_used == 100


def test_usage_store_check_rate_limit():
    """Test UsageStore check_rate_limit."""
    store = UsageStore()
    
    # First request should be allowed
    allowed, count = store.check_rate_limit("key1", limit_rpm=10)
    assert allowed
    assert count == 1
    
    # Subsequent requests within limit should be allowed
    for _ in range(9):
        allowed, count = store.check_rate_limit("key1", limit_rpm=10)
        assert allowed
    
    # 11th request should be denied
    allowed, count = store.check_rate_limit("key1", limit_rpm=10)
    assert not allowed
    assert count == 10


def test_usage_store_check_rate_limit_window_reset():
    """Test UsageStore rate limit window reset."""
    store = UsageStore()
    
    # Fill up the window
    for _ in range(5):
        store.check_rate_limit("key1", limit_rpm=5, window_seconds=1.0)
    
    # Wait for window to expire
    time.sleep(1.1)
    
    # Should be allowed again
    allowed, count = store.check_rate_limit("key1", limit_rpm=5, window_seconds=1.0)
    assert allowed
    assert count == 1


def test_usage_store_get_all_records():
    """Test UsageStore get_all_records."""
    store = UsageStore()
    
    store.increment_requests("key1", count=5)
    store.increment_requests("key2", count=10)
    
    records = store.get_all_records()
    assert len(records) == 2
    assert "key1" in records
    assert "key2" in records
    assert records["key1"].requests_count == 5
    assert records["key2"].requests_count == 10


def test_usage_store_clear():
    """Test UsageStore clear."""
    store = UsageStore()
    
    store.increment_requests("key1", count=5)
    store.increment_requests("key2", count=10)
    
    store.clear()
    
    records = store.get_all_records()
    assert len(records) == 0


def test_file_backed_usage_store_persistence():
    """Test FileBackedUsageStore saves and loads data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "usage.json"
        
        # Create store and add data
        store = FileBackedUsageStore(file_path, auto_save_interval=0.1)
        store.increment_requests("key1", count=100)
        store.increment_da_bytes("key1", 2048)
        store.save()
        
        # Create new store from same file
        store2 = FileBackedUsageStore(file_path)
        record = store2.get_record("key1")
        assert record.requests_count == 100
        assert record.da_bytes_posted == 2048


def test_file_backed_usage_store_auto_save():
    """Test FileBackedUsageStore auto-save."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "usage.json"
        
        # Create store with short auto-save interval
        store = FileBackedUsageStore(file_path, auto_save_interval=0.1)
        store.increment_requests("key1", count=50)
        
        # Wait for auto-save
        time.sleep(0.2)
        
        # Create new store and verify data was saved
        store2 = FileBackedUsageStore(file_path)
        record = store2.get_record("key1")
        assert record.requests_count == 50


def test_file_backed_usage_store_missing_file():
    """Test FileBackedUsageStore handles missing file gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "nonexistent.json"
        
        # Should not raise error
        store = FileBackedUsageStore(file_path)
        
        # Should work normally
        store.increment_requests("key1", count=10)
        record = store.get_record("key1")
        assert record.requests_count == 10
