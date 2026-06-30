"""
Snapshot Orchestrator - Unified automation for the complete snapshot lifecycle.

This module provides a single point of control for:
1. Automatic snapshot creation at intervals
2. Snapshot health monitoring and verification
3. Automatic cleanup of old snapshots
4. Snapshot discovery and sync coordination
5. Failure recovery and retry logic
6. Status reporting and diagnostics

The orchestrator runs as a background service and ensures snapshots are:
- Created automatically at configured intervals
- Verified for integrity
- Cleaned up when disk space is low
- Shared via RPC for peer discovery
- Used automatically for fast sync
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.snapshot.inventory import rebuild_inventory, remove_snapshot, upsert_snapshot
from core.snapshot.paths import get_snapshots_dir, snapshot_path_display

_log = logging.getLogger("animica.snapshot.orchestrator")


@dataclass
class SnapshotConfig:
    """Configuration for snapshot orchestration."""
    
    # Creation settings
    interval: int = 1000  # Blocks between snapshots
    auto_create: bool = True
    
    # Storage settings
    data_dir: Optional[Path] = None
    max_snapshots: int = 10  # Keep last N snapshots
    min_disk_space_gb: float = 10.0  # Min free space before cleanup
    
    # Sync settings
    sync_enabled: bool = True
    min_height_for_sync: int = 1000
    
    # Health check settings
    health_check_interval: int = 300  # Seconds between health checks
    verify_on_create: bool = True
    
    # Retry settings
    max_retries: int = 3
    retry_delay: int = 60  # Seconds between retries
    
    @classmethod
    def from_env(cls) -> SnapshotConfig:
        """Load configuration from environment variables."""
        return cls(
            interval=int(os.getenv("ANIMICA_SNAPSHOT_INTERVAL", "1000")),
            auto_create=os.getenv("ANIMICA_SNAPSHOT_AUTO_CREATE", "true").lower() in ("true", "1", "yes"),
            data_dir=Path(os.getenv("ANIMICA_DATA_DIR", "~/.animica")).expanduser() if os.getenv("ANIMICA_DATA_DIR") else None,
            max_snapshots=int(os.getenv("ANIMICA_SNAPSHOT_MAX_KEEP", "10")),
            min_disk_space_gb=float(os.getenv("ANIMICA_SNAPSHOT_MIN_DISK_GB", "10.0")),
            sync_enabled=os.getenv("ANIMICA_SNAPSHOT_SYNC_ENABLED", "true").lower() in ("true", "1", "yes"),
            min_height_for_sync=int(os.getenv("ANIMICA_SNAPSHOT_MIN_HEIGHT", "1000")),
            health_check_interval=int(os.getenv("ANIMICA_SNAPSHOT_HEALTH_INTERVAL", "300")),
            verify_on_create=os.getenv("ANIMICA_SNAPSHOT_VERIFY_ON_CREATE", "true").lower() in ("true", "1", "yes"),
            max_retries=int(os.getenv("ANIMICA_SNAPSHOT_MAX_RETRIES", "3")),
            retry_delay=int(os.getenv("ANIMICA_SNAPSHOT_RETRY_DELAY", "60")),
        )


@dataclass
class SnapshotStatus:
    """Current status of the snapshot system."""
    
    total_snapshots: int = 0
    last_snapshot_height: int = 0
    last_snapshot_time: float = 0.0
    last_health_check: float = 0.0
    healthy: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    
    # Statistics
    snapshots_created: int = 0
    snapshots_deleted: int = 0
    snapshots_failed: int = 0
    sync_attempts: int = 0
    sync_successes: int = 0


class SnapshotOrchestrator:
    """
    Main orchestrator for automated snapshot management.
    
    This class coordinates all snapshot-related activities:
    - Monitors chain height and triggers snapshot creation
    - Performs health checks on existing snapshots
    - Cleans up old snapshots when needed
    - Tracks and reports status
    - Handles failures with retry logic
    """
    
    def __init__(
        self,
        block_db: Any,
        state_db: Any,
        chain_id: int,
        config: Optional[SnapshotConfig] = None,
    ):
        """
        Initialize the snapshot orchestrator.
        
        Args:
            block_db: Block database instance
            state_db: State database instance
            chain_id: Chain ID
            config: Configuration (defaults to environment-based config)
        """
        self.block_db = block_db
        self.state_db = state_db
        self.chain_id = chain_id
        self.config = config or SnapshotConfig.from_env()
        self.status = SnapshotStatus()
        
        self._running = False
        self._tasks: list[asyncio.Task] = []
        
        _log.info(
            f"Snapshot orchestrator initialized: "
            f"interval={self.config.interval}, "
            f"auto_create={self.config.auto_create}, "
            f"max_keep={self.config.max_snapshots}"
        )
    
    def get_snapshots_dir(self) -> Path:
        """Get the snapshots directory path."""
        return get_snapshots_dir(self.config.data_dir)
    
    def list_snapshots(self) -> list[dict[str, Any]]:
        """
        List all existing snapshots.
        
        Returns:
            List of snapshot metadata dictionaries
        """
        snapshots_dir = self.get_snapshots_dir()
        entries = rebuild_inventory(snapshots_dir)
        snapshots = []
        for entry in entries:
            if entry.chain_id != self.chain_id:
                continue
            snapshots.append(
                {
                    "height": entry.checkpoint_height,
                    "path": entry.path,
                    "path_display": snapshot_path_display(Path(entry.path)),
                    "timestamp": entry.timestamp,
                    "size_bytes": entry.total_size,
                    "manifest_hash": entry.manifest_hash,
                    "created_at": entry.created_at,
                }
            )
        snapshots.sort(key=lambda s: s["height"], reverse=True)
        return snapshots
    
    def _get_current_height(self) -> Optional[int]:
        try:
            height = self.block_db.get_canonical_height()
        except Exception as exc:
            _log.warning(f"Failed to read canonical height: {exc}")
            return None
        if height is None:
            return None
        return int(height)

    def _latest_snapshot_height(self, snapshots: list[dict[str, Any]]) -> int:
        return snapshots[0]["height"] if snapshots else 0

    def _snapshot_target_height(
        self,
        *,
        head_height: int,
        last_snapshot_height: int,
    ) -> Optional[int]:
        if head_height <= 0 or self.config.interval <= 0:
            return None
        if head_height < self.config.interval:
            return None
        target_height = head_height - (head_height % self.config.interval)
        if target_height <= last_snapshot_height:
            return None
        return target_height

    def should_create_snapshot(self, *, head_height: int, last_snapshot_height: int) -> bool:
        """
        Determine if a snapshot should be created at the given height.
        
        Args:
            head_height: Current canonical head height
            last_snapshot_height: Latest snapshot height
            
        Returns:
            True if snapshot should be created
        """
        if not self.config.auto_create:
            return False
        
        target = self._snapshot_target_height(
            head_height=head_height,
            last_snapshot_height=last_snapshot_height,
        )
        return target is not None
    
    async def create_snapshot(self, height: int) -> tuple[bool, Optional[str]]:
        """
        Create a snapshot at the specified height.
        
        Args:
            height: Block height for snapshot
            
        Returns:
            Tuple of (success, error_message)
        """
        try:
            _log.info(f"Creating snapshot at height {height}")
            start_time = time.perf_counter()
            
            # Import snapshot creation function
            from core.db.snapshot import export_snapshot
            
            # Get snapshots directory
            snapshots_dir = self.get_snapshots_dir()
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            
            # Create snapshot directory
            snapshot_dir = snapshots_dir / f"chain-{self.chain_id}-height-{height}"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            
            # Export snapshot. export_snapshot returns a SnapshotManifest on
            # success and raises on failure; the outer try/except below maps
            # exceptions to (False, error).
            await asyncio.to_thread(
                export_snapshot,
                block_db=self.block_db,
                state_db=self.state_db,
                checkpoint_height=height,
                output_dir=snapshot_dir,
                compress=True,
            )

            elapsed = time.perf_counter() - start_time
            _log.info(f"Snapshot created successfully at height {height} (elapsed: {elapsed:.1f}s)")
            self.status.snapshots_created += 1
            self.status.last_snapshot_height = height
            self.status.last_snapshot_time = time.time()
            upsert_snapshot(snapshot_dir, snapshots_dir=self.get_snapshots_dir())

            # Verify if configured
            if self.config.verify_on_create:
                verified, error = await self.verify_snapshot(height)
                if not verified:
                    _log.warning(f"Snapshot verification failed: {error}")
                    self.status.warnings.append(f"Snapshot {height} verification failed: {error}")

            return True, None
                
        except Exception as e:
            error_msg = str(e)
            _log.error(f"Exception during snapshot creation: {error_msg}", exc_info=True)
            self.status.snapshots_failed += 1
            return False, error_msg
    
    async def verify_snapshot(self, height: int) -> tuple[bool, Optional[str]]:
        """
        Verify the integrity of a snapshot.
        
        Args:
            height: Snapshot height to verify
            
        Returns:
            Tuple of (valid, error_message)
        """
        try:
            from core.db.snapshot import verify_snapshot
            
            snapshot_dir = self.get_snapshots_dir() / f"chain-{self.chain_id}-height-{height}"
            
            if not snapshot_dir.exists():
                return False, f"Snapshot directory not found: {snapshot_dir}"
            
            # core.db.snapshot.verify_snapshot returns a (is_valid, errors) tuple.
            is_valid, errors = await asyncio.to_thread(
                verify_snapshot,
                snapshot_dir=snapshot_dir,
            )

            if is_valid:
                return True, None
            return False, "; ".join(errors) if errors else "verification failed"
                
        except Exception as e:
            return False, str(e)
    
    async def cleanup_old_snapshots(self) -> int:
        """
        Remove old snapshots to free disk space.
        
        Keeps the most recent N snapshots (configured via max_snapshots).
        
        Returns:
            Number of snapshots deleted
        """
        snapshots = self.list_snapshots()
        
        if len(snapshots) <= self.config.max_snapshots:
            return 0
        
        # Delete oldest snapshots beyond max_keep
        to_delete = snapshots[self.config.max_snapshots:]
        deleted = 0
        
        for snapshot in to_delete:
            try:
                import shutil
                path = Path(snapshot["path"])
                if path.exists():
                    shutil.rmtree(path)
                    _log.info(f"Deleted old snapshot at height {snapshot['height']}")
                    deleted += 1
                    self.status.snapshots_deleted += 1
                    remove_snapshot(
                        chain_id=self.chain_id,
                        checkpoint_height=int(snapshot["height"]),
                        snapshots_dir=self.get_snapshots_dir(),
                    )
            except Exception as e:
                _log.warning(f"Failed to delete snapshot {snapshot['path']}: {e}")
        
        return deleted
    
    async def check_disk_space(self) -> tuple[float, bool]:
        """
        Check available disk space.
        
        Returns:
            Tuple of (free_space_gb, needs_cleanup)
        """
        import shutil
        
        snapshots_dir = self.get_snapshots_dir()
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        
        stat = shutil.disk_usage(snapshots_dir)
        free_gb = stat.free / (1024**3)
        needs_cleanup = free_gb < self.config.min_disk_space_gb
        
        return free_gb, needs_cleanup
    
    async def perform_health_check(self) -> bool:
        """
        Perform a health check on the snapshot system.
        
        Returns:
            True if healthy, False otherwise
        """
        self.status.errors.clear()
        self.status.warnings.clear()
        healthy = True
        
        try:
            # Check if snapshots directory exists
            snapshots_dir = self.get_snapshots_dir()
            if not snapshots_dir.exists():
                snapshots_dir.mkdir(parents=True, exist_ok=True)
            
            # List snapshots
            snapshots = self.list_snapshots()
            self.status.total_snapshots = len(snapshots)
            
            # Check disk space
            free_gb, needs_cleanup = await self.check_disk_space()
            if needs_cleanup:
                self.status.warnings.append(f"Low disk space: {free_gb:.1f} GB free")
                await self.cleanup_old_snapshots()
            
            # Get current chain height
            current_height = self._get_current_height()
            if current_height is not None:
                # Check if we're behind on snapshots
                if snapshots:
                    last_snapshot_height = snapshots[0]["height"]
                    expected_next = (
                        ((last_snapshot_height // self.config.interval) + 1)
                        * self.config.interval
                    )

                    if current_height >= expected_next + self.config.interval:
                        self.status.warnings.append(
                            f"Behind on snapshots: current={current_height}, "
                            f"last_snapshot={last_snapshot_height}"
                        )
                elif current_height >= self.config.interval:
                    self.status.warnings.append(
                        f"No snapshots exist yet (height={current_height})"
                    )
            
            self.status.last_health_check = time.time()
            self.status.healthy = len(self.status.errors) == 0
            
            return self.status.healthy
            
        except Exception as e:
            _log.error(f"Health check failed: {e}", exc_info=True)
            self.status.errors.append(f"Health check exception: {e}")
            self.status.healthy = False
            return False
    
    async def monitor_and_create_snapshots(self):
        """
        Background task to monitor chain and create snapshots automatically.
        """
        _log.info("Snapshot monitor started")
        last_checked_height = 0
        
        while self._running:
            try:
                # Get current chain height
                current_height = self._get_current_height()
                if current_height is None:
                    await asyncio.sleep(10)
                    continue
                snapshots = self.list_snapshots()
                last_snapshot_height = self._latest_snapshot_height(snapshots)
                
                # Check if we've advanced to a new snapshot interval
                if current_height > last_checked_height:
                    target_height = self._snapshot_target_height(
                        head_height=current_height,
                        last_snapshot_height=last_snapshot_height,
                    )
                    if target_height is None:
                        if current_height < self.config.interval:
                            _log.info(
                                "No snapshots yet; head below interval",
                                extra={
                                    "head": current_height,
                                    "interval": self.config.interval,
                                    "next_snapshot": self.config.interval,
                                },
                            )
                        else:
                            _log.debug(
                                "Snapshot not due yet",
                                extra={
                                    "head": current_height,
                                    "interval": self.config.interval,
                                    "last_snapshot": last_snapshot_height,
                                },
                            )
                    elif self.should_create_snapshot(
                        head_height=current_height,
                        last_snapshot_height=last_snapshot_height,
                    ):
                        # Try to create with retries
                        for attempt in range(self.config.max_retries):
                            success, error = await self.create_snapshot(target_height)
                            if success:
                                break
                            
                            _log.warning(
                                f"Snapshot creation attempt {attempt + 1}/{self.config.max_retries} failed: {error}"
                            )
                            
                            if attempt < self.config.max_retries - 1:
                                await asyncio.sleep(self.config.retry_delay)
                    
                    last_checked_height = current_height
                
                # Sleep before next check (check every 10 seconds)
                await asyncio.sleep(10)
                
            except Exception as e:
                _log.error(f"Error in snapshot monitor: {e}", exc_info=True)
                await asyncio.sleep(30)  # Back off on errors
    
    async def periodic_health_check(self):
        """
        Background task to perform periodic health checks.
        """
        _log.info("Periodic health check started")
        
        while self._running:
            try:
                await self.perform_health_check()
                await asyncio.sleep(self.config.health_check_interval)
            except Exception as e:
                _log.error(f"Error in periodic health check: {e}", exc_info=True)
                await asyncio.sleep(60)  # Back off on errors
    
    async def start(self):
        """Start the orchestrator background tasks."""
        if self._running:
            _log.warning("Orchestrator already running")
            return
        
        _log.info("Starting snapshot orchestrator")
        self._running = True
        
        # Perform initial health check
        await self.perform_health_check()
        
        # Start background tasks
        if self.config.auto_create:
            task = asyncio.create_task(self.monitor_and_create_snapshots())
            self._tasks.append(task)
        
        task = asyncio.create_task(self.periodic_health_check())
        self._tasks.append(task)
        
        _log.info(f"Snapshot orchestrator started with {len(self._tasks)} background tasks")
    
    async def stop(self):
        """Stop the orchestrator background tasks."""
        if not self._running:
            return
        
        _log.info("Stopping snapshot orchestrator")
        self._running = False
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        
        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        
        self._tasks.clear()
        _log.info("Snapshot orchestrator stopped")
    
    def get_status(self) -> dict[str, Any]:
        """
        Get current status of the snapshot system.
        
        Returns:
            Status dictionary
        """
        snapshots = self.list_snapshots()
        last_snapshot_height = self._latest_snapshot_height(snapshots)
        head_height = self._get_current_height()
        next_snapshot_height: Optional[int] = None
        if self.config.interval > 0:
            if head_height is None:
                next_snapshot_height = None
            elif head_height < self.config.interval:
                next_snapshot_height = self.config.interval
            else:
                candidate = head_height - (head_height % self.config.interval)
                if candidate <= last_snapshot_height:
                    candidate += self.config.interval
                next_snapshot_height = candidate
        
        return {
            "config": {
                "interval": self.config.interval,
                "auto_create": self.config.auto_create,
                "max_snapshots": self.config.max_snapshots,
                "sync_enabled": self.config.sync_enabled,
            },
            "status": {
                "healthy": self.status.healthy,
                "total_snapshots": len(snapshots),
                "last_snapshot_height": last_snapshot_height,
                "last_snapshot_time": self.status.last_snapshot_time,
                "last_health_check": self.status.last_health_check,
                "head_height": head_height,
                "next_snapshot_height": next_snapshot_height,
            },
            "statistics": {
                "snapshots_created": self.status.snapshots_created,
                "snapshots_deleted": self.status.snapshots_deleted,
                "snapshots_failed": self.status.snapshots_failed,
                "sync_attempts": self.status.sync_attempts,
                "sync_successes": self.status.sync_successes,
            },
            "errors": self.status.errors,
            "warnings": self.status.warnings,
            "snapshots": [
                {
                    "height": s["height"],
                    "timestamp": s["timestamp"],
                    "size_mb": s["size_bytes"] / (1024**2),
                }
                for s in snapshots
            ],
        }
