"""
Automatic Snapshot Manager
===========================

This module manages automatic snapshot creation at regular block intervals.
Snapshots are created in the background to avoid blocking block processing.

Configuration:
- ANIMICA_SNAPSHOT_INTERVAL: Block interval for snapshot creation (default: 2000)
- ANIMICA_SNAPSHOT_ENABLED: Enable/disable automatic snapshots (default: true)
- ANIMICA_SNAPSHOT_RETENTION: Number of snapshots to retain (default: 5)
- ANIMICA_SNAPSHOT_DIR: Custom directory for snapshots (default: ~/.animica/snapshots)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional, Any
from concurrent.futures import ThreadPoolExecutor

_log = logging.getLogger("animica.snapshot_manager")

# Configuration from environment
SNAPSHOT_INTERVAL = int(os.environ.get("ANIMICA_SNAPSHOT_INTERVAL", "2000"))
SNAPSHOT_ENABLED = os.environ.get("ANIMICA_SNAPSHOT_ENABLED", "true").lower() in ("true", "1", "yes", "on")
SNAPSHOT_RETENTION = int(os.environ.get("ANIMICA_SNAPSHOT_RETENTION", "5"))
SNAPSHOT_DIR = os.environ.get("ANIMICA_SNAPSHOT_DIR", "")


class SnapshotManager:
    """
    Manages automatic snapshot creation at block intervals.
    
    Features:
    - Creates snapshots asynchronously to avoid blocking block import
    - Maintains a queue of pending snapshot tasks
    - Cleans up old snapshots based on retention policy
    - Thread-safe snapshot creation
    """
    
    def __init__(
        self,
        block_db: Any,
        state_db: Any,
        chain_id: int,
        interval: int = SNAPSHOT_INTERVAL,
        enabled: bool = SNAPSHOT_ENABLED,
        retention: int = SNAPSHOT_RETENTION,
        snapshot_dir: Optional[Path] = None,
    ):
        """
        Initialize the snapshot manager.
        
        Args:
            block_db: Block database instance
            state_db: State database instance
            chain_id: Chain ID
            interval: Block interval for snapshot creation
            enabled: Enable/disable automatic snapshots
            retention: Number of snapshots to retain
            snapshot_dir: Custom directory for snapshots
        """
        self.block_db = block_db
        self.state_db = state_db
        self.chain_id = chain_id
        self.interval = interval
        self.enabled = enabled
        self.retention = retention
        
        # Set snapshot directory
        if snapshot_dir:
            self.snapshot_dir = snapshot_dir
        elif SNAPSHOT_DIR:
            self.snapshot_dir = Path(SNAPSHOT_DIR)
        else:
            data_dir = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser()
            self.snapshot_dir = data_dir / "snapshots"
        
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        # Background task management
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot")
        self._pending_snapshots: set[int] = set()
        self._lock = threading.Lock()
        self._last_snapshot_height: Optional[int] = None
        
        _log.info(
            f"SnapshotManager initialized: interval={interval}, enabled={enabled}, "
            f"retention={retention}, dir={self.snapshot_dir}"
        )
    
    def should_create_snapshot(self, height: int) -> bool:
        """
        Check if a snapshot should be created at this height.
        
        Args:
            height: Current block height
            
        Returns:
            True if snapshot should be created
        """
        if not self.enabled:
            return False
        
        if height == 0:
            # Don't create snapshot at genesis
            return False
        
        # Check if height is at snapshot interval
        if height % self.interval != 0:
            return False
        
        with self._lock:
            # Don't create if already pending or recently created
            if height in self._pending_snapshots:
                return False
            
            if self._last_snapshot_height is not None and height <= self._last_snapshot_height:
                return False
        
        return True
    
    def create_snapshot_async(self, height: int) -> None:
        """
        Trigger asynchronous snapshot creation for the given height.
        
        This method returns immediately and snapshot creation happens in background.
        
        Args:
            height: Block height to snapshot
        """
        if not self.enabled:
            return
        
        with self._lock:
            if height in self._pending_snapshots:
                _log.debug(f"Snapshot at height {height} already pending")
                return
            
            self._pending_snapshots.add(height)
        
        _log.info(f"Scheduling snapshot creation at height {height}")
        
        # Submit to executor
        self._executor.submit(self._create_snapshot_task, height)
    
    def _create_snapshot_task(self, height: int) -> None:
        """
        Background task to create a snapshot.
        
        Args:
            height: Block height to snapshot
        """
        try:
            _log.info(f"Starting snapshot creation at height {height}")
            start_time = time.time()
            
            # Import here to avoid circular dependencies
            from core.db.snapshot import export_snapshot
            
            # Create snapshot directory
            snapshot_dir = self._get_snapshot_dir(self.chain_id, height)
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            
            # Export snapshot
            manifest = export_snapshot(
                block_db=self.block_db,
                state_db=self.state_db,
                checkpoint_height=height,
                output_dir=snapshot_dir,
                compress=True,
            )
            
            elapsed = time.time() - start_time
            
            _log.info(
                f"Snapshot created successfully at height {height}: "
                f"blocks={manifest.blocks_count}, accounts={manifest.accounts_count}, "
                f"elapsed={elapsed:.2f}s"
            )
            
            # Update tracking
            with self._lock:
                self._last_snapshot_height = height
                self._pending_snapshots.discard(height)
            
            # Cleanup old snapshots
            self._cleanup_old_snapshots()
            
        except Exception as e:
            _log.error(f"Failed to create snapshot at height {height}: {e}", exc_info=True)
            
            with self._lock:
                self._pending_snapshots.discard(height)
    
    def _get_snapshot_dir(self, chain_id: int, height: int) -> Path:
        """Get directory for a specific snapshot."""
        return self.snapshot_dir / f"chain-{chain_id}-height-{height}"
    
    def _cleanup_old_snapshots(self) -> None:
        """
        Clean up old snapshots based on retention policy.
        
        Keeps only the most recent N snapshots.
        """
        try:
            if self.retention <= 0:
                return  # No cleanup if retention is 0 or negative
            
            # List all snapshot directories for this chain
            snapshots = []
            for item in self.snapshot_dir.iterdir():
                if not item.is_dir():
                    continue
                
                # Parse directory name: chain-{id}-height-{height}
                if not item.name.startswith(f"chain-{self.chain_id}-height-"):
                    continue
                
                try:
                    parts = item.name.split("-")
                    if len(parts) == 4:
                        height = int(parts[3])
                        snapshots.append((height, item))
                except (ValueError, IndexError):
                    continue
            
            # Sort by height (descending)
            snapshots.sort(key=lambda x: x[0], reverse=True)
            
            # Remove old snapshots
            if len(snapshots) > self.retention:
                to_remove = snapshots[self.retention:]
                for height, snapshot_dir in to_remove:
                    try:
                        _log.info(f"Removing old snapshot at height {height}")
                        shutil.rmtree(snapshot_dir)
                    except Exception as e:
                        _log.warning(f"Failed to remove snapshot at height {height}: {e}")
        
        except Exception as e:
            _log.warning(f"Failed to cleanup old snapshots: {e}")
    
    def shutdown(self) -> None:
        """
        Shutdown the snapshot manager and wait for pending tasks.
        """
        _log.info("Shutting down snapshot manager...")
        self._executor.shutdown(wait=True)
        _log.info("Snapshot manager shutdown complete")
    
    def get_pending_count(self) -> int:
        """Get number of pending snapshot tasks."""
        with self._lock:
            return len(self._pending_snapshots)
    
    def get_last_snapshot_height(self) -> Optional[int]:
        """Get the height of the last completed snapshot."""
        with self._lock:
            return self._last_snapshot_height


# Global snapshot manager instance (initialized by BlockImporter)
_global_manager: Optional[SnapshotManager] = None
_manager_lock = threading.Lock()


def get_snapshot_manager() -> Optional[SnapshotManager]:
    """Get the global snapshot manager instance."""
    with _manager_lock:
        return _global_manager


def init_snapshot_manager(
    block_db: Any,
    state_db: Any,
    chain_id: int,
    **kwargs,
) -> SnapshotManager:
    """
    Initialize the global snapshot manager.
    
    This should be called once during node startup.
    """
    global _global_manager
    
    with _manager_lock:
        if _global_manager is not None:
            _log.warning("Snapshot manager already initialized, returning existing instance")
            return _global_manager
        
        _global_manager = SnapshotManager(
            block_db=block_db,
            state_db=state_db,
            chain_id=chain_id,
            **kwargs,
        )
        
        return _global_manager


def shutdown_snapshot_manager() -> None:
    """Shutdown the global snapshot manager."""
    global _global_manager
    
    with _manager_lock:
        if _global_manager is not None:
            _global_manager.shutdown()
            _global_manager = None


__all__ = [
    "SnapshotManager",
    "get_snapshot_manager",
    "init_snapshot_manager",
    "shutdown_snapshot_manager",
    "SNAPSHOT_INTERVAL",
    "SNAPSHOT_ENABLED",
    "SNAPSHOT_RETENTION",
]
