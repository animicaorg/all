"""
Tests for KV store batch operations (get_batch)
================================================

Tests that SQLiteKV and RocksKV implementations of get_batch work correctly
and provide performance improvements over sequential get() calls.
"""

import pytest
import tempfile
import os
from pathlib import Path

from core.db.sqlite import SQLiteKV
from core.db.rocksdb import RocksKV, _ROCKS_OK


class TestSQLiteKVBatch:
    """Test SQLiteKV batch operations."""

    @pytest.fixture
    def kv(self):
        """Create a temporary SQLite KV store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            kv = SQLiteKV(db_path)
            yield kv
            kv.close()

    def test_get_batch_empty(self, kv):
        """Test get_batch with empty key list."""
        result = kv.get_batch([])
        assert result == []

    def test_get_batch_single(self, kv):
        """Test get_batch with single key."""
        kv.put(b"key1", b"value1")
        result = kv.get_batch([b"key1"])
        assert result == [b"value1"]

    def test_get_batch_multiple_all_exist(self, kv):
        """Test get_batch with multiple keys that all exist."""
        kv.put(b"key1", b"value1")
        kv.put(b"key2", b"value2")
        kv.put(b"key3", b"value3")
        
        result = kv.get_batch([b"key1", b"key2", b"key3"])
        assert result == [b"value1", b"value2", b"value3"]

    def test_get_batch_some_missing(self, kv):
        """Test get_batch with some missing keys."""
        kv.put(b"key1", b"value1")
        kv.put(b"key3", b"value3")
        
        result = kv.get_batch([b"key1", b"key2", b"key3"])
        assert result == [b"value1", None, b"value3"]

    def test_get_batch_all_missing(self, kv):
        """Test get_batch with all missing keys."""
        result = kv.get_batch([b"missing1", b"missing2", b"missing3"])
        assert result == [None, None, None]

    def test_get_batch_order_preserved(self, kv):
        """Test that get_batch preserves order of input keys."""
        # Insert in one order
        kv.put(b"key1", b"value1")
        kv.put(b"key2", b"value2")
        kv.put(b"key3", b"value3")
        
        # Request in different order
        result = kv.get_batch([b"key3", b"key1", b"key2"])
        assert result == [b"value3", b"value1", b"value2"]

    def test_get_batch_duplicates(self, kv):
        """Test get_batch with duplicate keys in input."""
        kv.put(b"key1", b"value1")
        
        # Same key requested multiple times
        result = kv.get_batch([b"key1", b"key1", b"key1"])
        assert result == [b"value1", b"value1", b"value1"]

    def test_get_batch_large_batch(self, kv):
        """Test get_batch with large number of keys (>900 to test chunking)."""
        # Create 1500 keys to test chunking logic
        num_keys = 1500
        for i in range(num_keys):
            key = f"key{i:04d}".encode()
            value = f"value{i:04d}".encode()
            kv.put(key, value)
        
        # Request all keys
        keys = [f"key{i:04d}".encode() for i in range(num_keys)]
        result = kv.get_batch(keys)
        
        # Verify all values returned correctly
        assert len(result) == num_keys
        for i in range(num_keys):
            assert result[i] == f"value{i:04d}".encode()

    def test_get_batch_vs_sequential(self, kv):
        """Verify get_batch returns same results as sequential get() calls."""
        # Setup test data
        test_keys = [f"key{i}".encode() for i in range(100)]
        test_values = [f"value{i}".encode() for i in range(100)]
        
        # Insert every other key
        for i in range(0, 100, 2):
            kv.put(test_keys[i], test_values[i])
        
        # Compare batch vs sequential
        batch_result = kv.get_batch(test_keys)
        sequential_result = [kv.get(k) for k in test_keys]
        
        assert batch_result == sequential_result


@pytest.mark.skipif(not _ROCKS_OK, reason="RocksDB not available")
class TestRocksKVBatch:
    """Test RocksKV batch operations."""

    @pytest.fixture
    def kv(self):
        """Create a temporary RocksDB KV store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.rocksdb"
            from core.db.rocksdb import open_rocksdb_kv
            kv = open_rocksdb_kv(str(db_path))
            yield kv
            kv.close()

    def test_get_batch_empty(self, kv):
        """Test get_batch with empty key list."""
        result = kv.get_batch([])
        assert result == []

    def test_get_batch_single(self, kv):
        """Test get_batch with single key."""
        kv.put(b"key1", b"value1")
        result = kv.get_batch([b"key1"])
        assert result == [b"value1"]

    def test_get_batch_multiple_all_exist(self, kv):
        """Test get_batch with multiple keys that all exist."""
        kv.put(b"key1", b"value1")
        kv.put(b"key2", b"value2")
        kv.put(b"key3", b"value3")
        
        result = kv.get_batch([b"key1", b"key2", b"key3"])
        assert result == [b"value1", b"value2", b"value3"]

    def test_get_batch_some_missing(self, kv):
        """Test get_batch with some missing keys."""
        kv.put(b"key1", b"value1")
        kv.put(b"key3", b"value3")
        
        result = kv.get_batch([b"key1", b"key2", b"key3"])
        assert result == [b"value1", None, b"value3"]

    def test_get_batch_all_missing(self, kv):
        """Test get_batch with all missing keys."""
        result = kv.get_batch([b"missing1", b"missing2", b"missing3"])
        assert result == [None, None, None]

    def test_get_batch_order_preserved(self, kv):
        """Test that get_batch preserves order of input keys."""
        # Insert in one order
        kv.put(b"key1", b"value1")
        kv.put(b"key2", b"value2")
        kv.put(b"key3", b"value3")
        
        # Request in different order
        result = kv.get_batch([b"key3", b"key1", b"key2"])
        assert result == [b"value3", b"value1", b"value2"]

    def test_get_batch_duplicates(self, kv):
        """Test get_batch with duplicate keys in input."""
        kv.put(b"key1", b"value1")
        
        # Same key requested multiple times
        result = kv.get_batch([b"key1", b"key1", b"key1"])
        assert result == [b"value1", b"value1", b"value1"]

    def test_get_batch_large_batch(self, kv):
        """Test get_batch with large number of keys."""
        # Create 1500 keys
        num_keys = 1500
        for i in range(num_keys):
            key = f"key{i:04d}".encode()
            value = f"value{i:04d}".encode()
            kv.put(key, value)
        
        # Request all keys
        keys = [f"key{i:04d}".encode() for i in range(num_keys)]
        result = kv.get_batch(keys)
        
        # Verify all values returned correctly
        assert len(result) == num_keys
        for i in range(num_keys):
            assert result[i] == f"value{i:04d}".encode()

    def test_get_batch_vs_sequential(self, kv):
        """Verify get_batch returns same results as sequential get() calls."""
        # Setup test data
        test_keys = [f"key{i}".encode() for i in range(100)]
        test_values = [f"value{i}".encode() for i in range(100)]
        
        # Insert every other key
        for i in range(0, 100, 2):
            kv.put(test_keys[i], test_values[i])
        
        # Compare batch vs sequential
        batch_result = kv.get_batch(test_keys)
        sequential_result = [kv.get(k) for k in test_keys]
        
        assert batch_result == sequential_result
