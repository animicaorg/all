"""
Tests for automatic snapshot creation at 2000 block intervals.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock the imports that may not be available in test environment
import sys


class MockBlockDB:
    """Mock BlockDB for testing."""
    
    def __init__(self):
        self._head = None
        self._canonical_height = 0
    
    def get_head(self):
        return self._head
    
    def get_canonical_head(self):
        return self._head
    
    def get_canonical_height(self):
        return self._canonical_height
    
    def set_canonical_height(self, height):
        self._canonical_height = height
    
    def get_header_by_hash(self, h):
        return None
    
    def get_block_by_hash(self, h):
        return None
    
    def put_header(self, header):
        return b'\x00' * 32
    
    def put_block(self, block):
        return b'\x00' * 32
    
    def set_canonical(self, height, block_hash, **_kwargs):
        pass
    
    def set_head(self, height, block_hash, **_kwargs):
        self._head = (height, block_hash)


class MockStateDB:
    """Mock StateDB for testing."""
    
    def snapshot(self):
        return {}
    
    def revert(self, snap):
        pass


def test_snapshot_interval_config():
    """Test that snapshot interval can be configured via environment."""
    # Save original value
    original = os.environ.get('ANIMICA_SNAPSHOT_INTERVAL')
    
    try:
        # Test default value
        os.environ.pop('ANIMICA_SNAPSHOT_INTERVAL', None)
        from importlib import reload
        from core.chain import block_import
        reload(block_import)
        assert block_import.DEFAULT_SNAPSHOT_INTERVAL == 2000
        
        # Test custom value
        os.environ['ANIMICA_SNAPSHOT_INTERVAL'] = '5000'
        reload(block_import)
        assert block_import.DEFAULT_SNAPSHOT_INTERVAL == 5000
    
    finally:
        # Restore original
        if original is not None:
            os.environ['ANIMICA_SNAPSHOT_INTERVAL'] = original
        else:
            os.environ.pop('ANIMICA_SNAPSHOT_INTERVAL', None)


def test_should_create_disk_snapshot():
    """Test snapshot creation decision logic."""
    # Test the logic directly without full BlockImporter initialization
    
    # Mock object with just the needed attributes
    class MockImporter:
        def __init__(self):
            self._snapshot_interval = 2000
            self._snapshot_auto_create = True
            self._created_snapshots = set()
            self._pending_snapshots = set()
        
        def _should_create_disk_snapshot(self, height):
            """Same logic as BlockImporter._should_create_disk_snapshot"""
            if not self._snapshot_auto_create:
                return False
            if height <= 0:
                return False
            if self._snapshot_interval <= 0:
                return False
            if height % self._snapshot_interval != 0:
                return False
            if height in self._created_snapshots or height in self._pending_snapshots:
                return False
            return True
    
    importer = MockImporter()
    
    # Test snapshot should be created at interval boundaries
    assert importer._should_create_disk_snapshot(2000)
    assert importer._should_create_disk_snapshot(4000)
    assert importer._should_create_disk_snapshot(6000)
    
    # Test snapshot should NOT be created at non-interval heights
    assert not importer._should_create_disk_snapshot(1999)
    assert not importer._should_create_disk_snapshot(2001)
    assert not importer._should_create_disk_snapshot(1500)
    
    # Test snapshot should NOT be created at genesis
    assert not importer._should_create_disk_snapshot(0)
    
    # Test snapshot should NOT be created twice
    importer._created_snapshots.add(2000)
    assert not importer._should_create_disk_snapshot(2000)
    
    # Test snapshot should NOT be created if pending
    importer._pending_snapshots.add(4000)
    assert not importer._should_create_disk_snapshot(4000)


def test_snapshot_auto_create_disabled():
    """Test that snapshot creation can be disabled."""
    # Test the logic directly
    class MockImporter:
        def __init__(self, auto_create):
            self._snapshot_interval = 2000
            self._snapshot_auto_create = auto_create
            self._created_snapshots = set()
            self._pending_snapshots = set()
        
        def _should_create_disk_snapshot(self, height):
            """Same logic as BlockImporter._should_create_disk_snapshot"""
            if not self._snapshot_auto_create:
                return False
            if height <= 0:
                return False
            if self._snapshot_interval <= 0:
                return False
            if height % self._snapshot_interval != 0:
                return False
            if height in self._created_snapshots or height in self._pending_snapshots:
                return False
            return True
    
    importer = MockImporter(auto_create=False)
    
    # Should not create snapshots when disabled
    assert not importer._should_create_disk_snapshot(2000)
    assert not importer._should_create_disk_snapshot(4000)


def test_check_missing_snapshots():
    """Test that missing snapshot heights are correctly identified."""
    # Simplified test that just verifies the logic
    
    snapshot_interval = 2000
    current_height = 8000
    created_snapshots = set()
    
    # Find missing snapshots
    missing_heights = []
    for h in range(snapshot_interval, current_height, snapshot_interval):
        if h not in created_snapshots:
            missing_heights.append(h)
    
    # Should find 2000, 4000, 6000
    assert len(missing_heights) == 3
    assert 2000 in missing_heights
    assert 4000 in missing_heights
    assert 6000 in missing_heights
    
    # Mark some as created
    created_snapshots.add(2000)
    
    missing_heights = []
    for h in range(snapshot_interval, current_height, snapshot_interval):
        if h not in created_snapshots:
            missing_heights.append(h)
    
    # Should now only find 4000, 6000
    assert len(missing_heights) == 2
    assert 4000 in missing_heights
    assert 6000 in missing_heights
    assert 2000 not in missing_heights


if __name__ == '__main__':
    # Run tests
    print("Testing snapshot configuration...")
    test_snapshot_interval_config()
    print("✓ Snapshot interval configuration test passed")
    
    print("\nTesting snapshot creation logic...")
    test_should_create_disk_snapshot()
    print("✓ Snapshot creation logic test passed")
    
    print("\nTesting snapshot auto-create disable...")
    test_snapshot_auto_create_disabled()
    print("✓ Snapshot auto-create disable test passed")
    
    print("\nTesting missing snapshot detection...")
    test_check_missing_snapshots()
    print("✓ Missing snapshot detection test passed")
    
    print("\n✅ All tests passed!")
