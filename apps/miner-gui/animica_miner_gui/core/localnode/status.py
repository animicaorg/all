"""Status tracking for local node.

Defines data structures for node and sync status.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class NodeState(str, Enum):
    """Node process state."""
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class NodeStatus:
    """Status of the local node process."""
    state: NodeState
    pid: Optional[int] = None
    port: Optional[int] = None
    error: Optional[str] = None
    uptime_seconds: float = 0.0
    
    @property
    def is_running(self) -> bool:
        """Check if node is running."""
        return self.state in (NodeState.STARTING, NodeState.READY)
    
    @property
    def is_ready(self) -> bool:
        """Check if node is ready for RPC calls."""
        return self.state == NodeState.READY


@dataclass
class SyncStatus:
    """Blockchain sync status from the node."""
    syncing: bool
    current_height: int = 0
    best_height: int = 0
    phase: str = "idle"
    in_flight: int = 0
    queued: int = 0
    peer_count: int = 0
    peers_in: int = 0
    peers_out: int = 0
    last_progress: Optional[str] = None
    last_error: Optional[str] = None
    
    @property
    def progress_percent(self) -> float:
        """Calculate sync progress as percentage."""
        if self.best_height == 0:
            return 0.0
        return min(100.0, (self.current_height / self.best_height) * 100.0)
    
    @property
    def blocks_behind(self) -> int:
        """Number of blocks behind the network."""
        return max(0, self.best_height - self.current_height)
    
    @property
    def is_synced(self) -> bool:
        """Check if node is fully synced."""
        return not self.syncing and self.blocks_behind <= 1
