"""Miner runner - manages the mining process lifecycle and event streaming.

Responsible for:
- Starting/stopping the miner process or thread
- Streaming structured events (hashrate, templates, blocks, errors)
- Providing a unified event bus for the UI
- Reliable shutdown with signal handling and hard kill fallback
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MinerStatus(str, Enum):
    """Miner process status."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class EventType(str, Enum):
    """Mining event types."""
    STATUS_CHANGE = "status_change"
    HASHRATE_UPDATE = "hashrate_update"
    SHARE_FOUND = "share_found"
    BLOCK_FOUND = "block_found"
    TEMPLATE_UPDATE = "template_update"
    ERROR = "error"
    LOG = "log"
    DEVICE_UPDATE = "device_update"


@dataclass
class MiningEvent:
    """Structured mining event."""
    event_type: EventType
    timestamp: float
    data: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "data": self.data
        }


class MinerRunner:
    """Manages the mining process lifecycle and event streaming."""
    
    def __init__(self):
        self.status = MinerStatus.STOPPED
        self.process: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.event_callbacks: List[Callable[[MiningEvent], None]] = []
        self._lock = threading.RLock()
        self._last_hashrate = 0.0
        self._last_shares = 0
        self._last_blocks = 0
        self._start_time = 0.0
    
    def add_event_callback(self, callback: Callable[[MiningEvent], None]) -> None:
        """Add a callback for mining events.
        
        Args:
            callback: Function to call with each MiningEvent
        """
        with self._lock:
            self.event_callbacks.append(callback)
    
    def remove_event_callback(self, callback: Callable[[MiningEvent], None]) -> None:
        """Remove an event callback."""
        with self._lock:
            if callback in self.event_callbacks:
                self.event_callbacks.remove(callback)
    
    def _emit_event(self, event: MiningEvent) -> None:
        """Emit an event to all registered callbacks."""
        with self._lock:
            callbacks = list(self.event_callbacks)
        
        for callback in callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error in event callback: {e}")
    
    def _change_status(self, new_status: MinerStatus, reason: str = "") -> None:
        """Change miner status and emit event."""
        with self._lock:
            old_status = self.status
            self.status = new_status
        
        logger.info(f"Miner status: {old_status} -> {new_status}" + 
                   (f" ({reason})" if reason else ""))
        
        self._emit_event(MiningEvent(
            event_type=EventType.STATUS_CHANGE,
            timestamp=time.time(),
            data={"status": new_status.value, "reason": reason}
        ))
    
    def start(self, config: Dict[str, Any]) -> bool:
        """Start the mining process.
        
        Args:
            config: Mining configuration dictionary
        
        Returns:
            True if started successfully, False otherwise
        """
        with self._lock:
            if self.status != MinerStatus.STOPPED:
                logger.warning(f"Cannot start miner: status is {self.status}")
                return False
            
            self._change_status(MinerStatus.STARTING)
            self.stop_event.clear()
            self._start_time = time.time()
            self._last_hashrate = 0.0
            self._last_shares = 0
            self._last_blocks = 0
        
        try:
            # Start miner in a background thread
            self.thread = threading.Thread(
                target=self._run_miner_thread,
                args=(config,),
                daemon=True,
                name="MinerRunner"
            )
            self.thread.start()
            
            # Give it a moment to start
            time.sleep(0.5)
            
            with self._lock:
                if self.status == MinerStatus.STARTING:
                    self._change_status(MinerStatus.RUNNING)
            
            return True
            
        except Exception as e:
            logger.error(f"Error starting miner: {e}")
            self._change_status(MinerStatus.ERROR, str(e))
            return False
    
    def stop(self, timeout: float = 10.0) -> bool:
        """Stop the mining process.
        
        Args:
            timeout: Maximum time to wait for graceful shutdown
        
        Returns:
            True if stopped successfully, False otherwise
        """
        with self._lock:
            if self.status == MinerStatus.STOPPED:
                return True
            
            self._change_status(MinerStatus.STOPPING)
            self.stop_event.set()
        
        # Try graceful shutdown first
        if self.process:
            try:
                logger.info("Sending SIGTERM to miner process")
                self.process.terminate()
                
                # Wait for process to exit
                try:
                    self.process.wait(timeout=timeout / 2)
                    logger.info("Miner process terminated gracefully")
                except subprocess.TimeoutExpired:
                    logger.warning("Miner process did not terminate, sending SIGKILL")
                    self.process.kill()
                    self.process.wait(timeout=timeout / 2)
                    logger.info("Miner process killed")
            except Exception as e:
                logger.error(f"Error stopping miner process: {e}")
        
        # Wait for thread to finish
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout / 2)
            
            if self.thread.is_alive():
                logger.warning("Miner thread did not finish in time")
                # Can't force-kill a Python thread, but we've tried
        
        with self._lock:
            self.process = None
            self.thread = None
            self._change_status(MinerStatus.STOPPED)
        
        return True
    
    def _run_miner_thread(self, config: Dict[str, Any]) -> None:
        """Run the miner in a background thread.
        
        This simulates a mining process. In a real implementation,
        this would either:
        1. Call the mining orchestrator API directly (in-process)
        2. Start a subprocess and parse its JSON-RPC/stdio output
        
        For now, we'll simulate mining with periodic updates.
        """
        logger.info("Miner thread started")
        
        try:
            # Simulate mining activity
            cycle = 0
            while not self.stop_event.is_set():
                cycle += 1
                time.sleep(2.0)
                
                # Simulate hashrate updates
                hashrate = 1000000 * (1 + (cycle % 10) / 10.0)  # 1-2 MH/s
                self._last_hashrate = hashrate
                
                self._emit_event(MiningEvent(
                    event_type=EventType.HASHRATE_UPDATE,
                    timestamp=time.time(),
                    data={"hashrate": hashrate, "unit": "H/s"}
                ))
                
                # Simulate occasional shares
                if cycle % 5 == 0:
                    self._last_shares += 1
                    self._emit_event(MiningEvent(
                        event_type=EventType.SHARE_FOUND,
                        timestamp=time.time(),
                        data={"share_count": self._last_shares}
                    ))
                
                # Simulate rare blocks
                if cycle % 30 == 0:
                    self._last_blocks += 1
                    self._emit_event(MiningEvent(
                        event_type=EventType.BLOCK_FOUND,
                        timestamp=time.time(),
                        data={"block_count": self._last_blocks, "height": 1000 + self._last_blocks}
                    ))
                
                # Simulate template updates
                if cycle % 10 == 0:
                    self._emit_event(MiningEvent(
                        event_type=EventType.TEMPLATE_UPDATE,
                        timestamp=time.time(),
                        data={
                            "height": 1000 + cycle // 10,
                            "transactions": (cycle % 50) + 10
                        }
                    ))
                
                # Simulate log messages
                if cycle % 3 == 0:
                    self._emit_event(MiningEvent(
                        event_type=EventType.LOG,
                        timestamp=time.time(),
                        data={
                            "level": "info",
                            "message": f"Mining cycle {cycle} completed",
                            "component": "miner"
                        }
                    ))
        
        except Exception as e:
            logger.error(f"Error in miner thread: {e}")
            self._emit_event(MiningEvent(
                event_type=EventType.ERROR,
                timestamp=time.time(),
                data={"error": str(e)}
            ))
        
        finally:
            logger.info("Miner thread finished")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current mining statistics.
        
        Returns:
            Dictionary with current stats
        """
        with self._lock:
            uptime = time.time() - self._start_time if self._start_time > 0 else 0.0
            
            return {
                "status": self.status.value,
                "hashrate": self._last_hashrate,
                "shares": self._last_shares,
                "blocks": self._last_blocks,
                "uptime_seconds": uptime
            }
    
    def is_running(self) -> bool:
        """Check if miner is currently running."""
        with self._lock:
            return self.status == MinerStatus.RUNNING


# Global miner runner instance
_runner: Optional[MinerRunner] = None


def get_runner() -> MinerRunner:
    """Get the global miner runner instance."""
    global _runner
    if _runner is None:
        _runner = MinerRunner()
    return _runner
