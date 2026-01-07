"""
Tests for automatic snapshot manager.
"""

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class MockBlockDB:
    """Mock block database for testing."""
    
    def get_block_hash_by_height(self, height):
        return b"\x00" * 32
    
    def get_head(self):
        return (100, b"\x00" * 32)


class MockStateDB:
    """Mock state database for testing."""
    
    class MockDB:
        def scan(self, prefix):
            return []
    
    def __init__(self):
        self._db = self.MockDB()


def test_snapshot_manager_initialization():
    """Test that SnapshotManager can be initialized."""
    from core.chain.snapshot_manager import SnapshotManager
    
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SnapshotManager(
            block_db=MockBlockDB(),
            state_db=MockStateDB(),
            chain_id=1,
            interval=2000,
            enabled=True,
            retention=5,
            snapshot_dir=Path(tmpdir),
        )
        
        assert manager.interval == 2000
        assert manager.enabled is True
        assert manager.retention == 5
        assert manager.chain_id == 1
        
        manager.shutdown()


def test_snapshot_manager_should_create_snapshot():
    """Test snapshot creation decision logic."""
    from core.chain.snapshot_manager import SnapshotManager
    
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SnapshotManager(
            block_db=MockBlockDB(),
            state_db=MockStateDB(),
            chain_id=1,
            interval=2000,
            enabled=True,
            snapshot_dir=Path(tmpdir),
        )
        
        # Should not create at genesis
        assert not manager.should_create_snapshot(0)
        
        # Should not create at non-interval heights
        assert not manager.should_create_snapshot(1000)
        assert not manager.should_create_snapshot(1999)
        assert not manager.should_create_snapshot(2001)
        
        # Should create at interval heights
        assert manager.should_create_snapshot(2000)
        assert manager.should_create_snapshot(4000)
        assert manager.should_create_snapshot(6000)
        
        manager.shutdown()


def test_snapshot_manager_disabled():
    """Test that disabled manager doesn't create snapshots."""
    from core.chain.snapshot_manager import SnapshotManager
    
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SnapshotManager(
            block_db=MockBlockDB(),
            state_db=MockStateDB(),
            chain_id=1,
            interval=2000,
            enabled=False,
            snapshot_dir=Path(tmpdir),
        )
        
        # Should never create when disabled
        assert not manager.should_create_snapshot(2000)
        assert not manager.should_create_snapshot(4000)
        
        manager.shutdown()


def test_snapshot_manager_pending_tracking():
    """Test that pending snapshots are tracked correctly."""
    from core.chain.snapshot_manager import SnapshotManager
    
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SnapshotManager(
            block_db=MockBlockDB(),
            state_db=MockStateDB(),
            chain_id=1,
            interval=2000,
            enabled=True,
            snapshot_dir=Path(tmpdir),
        )
        
        # Initially no pending snapshots
        assert manager.get_pending_count() == 0
        
        # Mock the export to avoid actually creating snapshots
        with patch("core.chain.snapshot_manager.export_snapshot") as mock_export:
            mock_manifest = MagicMock()
            mock_manifest.blocks_count = 100
            mock_manifest.accounts_count = 10
            mock_export.return_value = mock_manifest
            
            # Trigger snapshot creation
            manager.create_snapshot_async(2000)
            
            # Should be pending now
            assert manager.get_pending_count() == 1
            
            # Wait for completion (with timeout)
            timeout = 5
            start = time.time()
            while manager.get_pending_count() > 0 and (time.time() - start) < timeout:
                time.sleep(0.1)
            
            # Should be completed
            assert manager.get_pending_count() == 0
            assert manager.get_last_snapshot_height() == 2000
        
        manager.shutdown()


def test_snapshot_manager_duplicate_prevention():
    """Test that duplicate snapshots are prevented."""
    from core.chain.snapshot_manager import SnapshotManager
    
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SnapshotManager(
            block_db=MockBlockDB(),
            state_db=MockStateDB(),
            chain_id=1,
            interval=2000,
            enabled=True,
            snapshot_dir=Path(tmpdir),
        )
        
        with patch("core.chain.snapshot_manager.export_snapshot") as mock_export:
            mock_manifest = MagicMock()
            mock_manifest.blocks_count = 100
            mock_manifest.accounts_count = 10
            mock_export.return_value = mock_manifest
            
            # First creation should work
            assert manager.should_create_snapshot(2000)
            manager.create_snapshot_async(2000)
            
            # Second creation at same height should be prevented
            assert not manager.should_create_snapshot(2000)
        
        manager.shutdown()


def test_snapshot_manager_global_init():
    """Test global snapshot manager initialization."""
    from core.chain.snapshot_manager import (
        init_snapshot_manager,
        get_snapshot_manager,
        shutdown_snapshot_manager,
    )
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize global manager
        manager = init_snapshot_manager(
            block_db=MockBlockDB(),
            state_db=MockStateDB(),
            chain_id=1,
            snapshot_dir=Path(tmpdir),
        )
        
        # Should be able to get it
        assert get_snapshot_manager() is manager
        
        # Shutdown
        shutdown_snapshot_manager()
        
        # Should be None after shutdown
        assert get_snapshot_manager() is None


def test_snapshot_manager_cleanup():
    """Test snapshot cleanup based on retention policy."""
    from core.chain.snapshot_manager import SnapshotManager
    
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = Path(tmpdir)
        
        # Create some fake snapshot directories
        for height in [2000, 4000, 6000, 8000, 10000, 12000]:
            snap_dir = snapshot_dir / f"chain-1-height-{height}"
            snap_dir.mkdir(parents=True)
            # Create a dummy file
            (snap_dir / "manifest.json").write_text("{}")
        
        manager = SnapshotManager(
            block_db=MockBlockDB(),
            state_db=MockStateDB(),
            chain_id=1,
            interval=2000,
            enabled=True,
            retention=3,  # Keep only 3 snapshots
            snapshot_dir=snapshot_dir,
        )
        
        # Trigger cleanup
        manager._cleanup_old_snapshots()
        
        # Should keep only the 3 most recent
        existing = sorted([
            int(d.name.split("-")[-1])
            for d in snapshot_dir.iterdir()
            if d.is_dir() and d.name.startswith("chain-1-")
        ])
        
        assert len(existing) == 3
        assert existing == [8000, 10000, 12000]
        
        manager.shutdown()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
