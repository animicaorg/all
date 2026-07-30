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
import math
import os
import re
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
        self._last_blocks = 0
        self._start_time = 0.0
        self._block_times = []  # Track recent block timestamps for hashrate calculation
        self._current_theta_micro = 0  # Current network difficulty
    
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
    
    def _calculate_hashrate_from_blocks(self) -> float:
        """Calculate hashrate based on block finding rate and difficulty.
        
        Returns:
            Estimated hashrate in H/s
        """
        with self._lock:
            # Keep only recent blocks (last 10 minutes)
            now = time.time()
            cutoff = now - 600  # 10 minutes
            self._block_times = [t for t in self._block_times if t >= cutoff]
            
            if len(self._block_times) < 2:
                return 0.0
            
            # Calculate block rate (blocks per second)
            time_span = self._block_times[-1] - self._block_times[0]
            if time_span <= 0:
                return 0.0
            
            block_count = len(self._block_times)
            blocks_per_second = block_count / time_span
            
            # Estimate hashrate from block rate and difficulty
            # Each block requires on average 1/probability hashes
            # probability = e^(-threshold) where threshold = theta
            # Therefore: hashrate = blocks_per_second / e^(-threshold)
            
            if self._current_theta_micro > 0:
                # Use exponential relationship for accurate calculation
                # e^(-t) where t is in micro-nats, so convert: t_nats = t_micro / 1e6
                threshold_nats = self._current_theta_micro / 1_000_000
                probability = math.exp(-threshold_nats)
                if probability > 0:
                    hashrate = blocks_per_second / probability
                    return hashrate
            
            # If no difficulty info, use a reasonable estimate
            # Typical difficulty is around 1e6 micro-nats (theta=1.0 nats)
            # This gives probability ≈ 0.368, so hashrate ≈ blocks_per_second * 2.72
            return blocks_per_second * 2.72
    
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
            self._last_blocks = 0
            self._block_times = []
            self._current_theta_micro = 0
        
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
        
        This runs the actual mining CLI as a subprocess and monitors its output.
        
        Uses the 'mine-blocks' command in a continuous loop for batch-based mining:
        - Mines a configurable number of blocks per batch (blocks_per_batch)
        - Automatically restarts after each batch completes
        - Retries with configurable delay on errors (retry_delay)
        - Checks stop_event regularly for fast shutdown response
        """
        logger.info("Miner thread started")
        
        try:
            # Get configuration
            payout_address = config.get('miner', {}).get('payout_address')
            if not payout_address:
                logger.error("No payout address configured")
                self._emit_event(MiningEvent(
                    event_type=EventType.ERROR,
                    timestamp=time.time(),
                    data={"error": "No payout address configured"}
                ))
                return
            
            from animica_miner_gui.backend.config import resolve_rpc_url
            rpc_url = resolve_rpc_url(config) or "http://127.0.0.1:8545"
            threads = config.get('cpu', {}).get('threads', 1)
            blocks_per_batch = config.get('miner', {}).get('blocks_per_batch', 10)
            
            # Find the repository root (where mining module exists)
            repo_root = None
            try:
                import mining
                mining_path = Path(mining.__file__).parent.parent.resolve()
                if (mining_path / "mining").is_dir():
                    repo_root = str(mining_path)
                    logger.info(f"Found mining module at: {repo_root}")
            except ImportError:
                pass
            
            if not repo_root:
                current_file = Path(__file__).resolve()
                potential_root = current_file.parent.parent.parent.parent.parent
                if (potential_root / "mining" / "__init__.py").is_file():
                    repo_root = str(potential_root)
                    logger.info(f"Found repository root at: {repo_root}")
            
            # Prepare environment
            current_pythonpath = os.environ.get('PYTHONPATH', '')
            if repo_root:
                if current_pythonpath:
                    pythonpath = f"{repo_root}{os.pathsep}{current_pythonpath}"
                else:
                    pythonpath = repo_root
            else:
                pythonpath = current_pythonpath
                logger.warning("Could not locate repository root; mining module may not be found")
            
            minimal_env = {
                'PATH': os.environ.get('PATH', ''),
                'HOME': os.environ.get('HOME', ''),
                'USER': os.environ.get('USER', ''),
                'PYTHONPATH': pythonpath,
                'ANIMICA_PAYOUT_ADDRESS': payout_address
            }
            
            wallet_file = config.get('miner', {}).get('wallet_file')
            if wallet_file:
                minimal_env['ANIMICA_WALLETS_FILE'] = wallet_file
                logger.info(f"Using custom wallet file: {wallet_file}")
            
            logger.info(f"Subprocess PYTHONPATH: {pythonpath}")
            
            # Build command once outside the loop (optimization)
            # `sys.executable` is the GUI binary in a frozen build, so a bare
            # `-m mining.cli.miner` spawned GUI copies in a restart loop
            # instead of miners. resolve_module_cmd re-enters the binary via
            # --run-module, which runs the bundled module for real.
            from animica_miner_gui.backend.cli_runner import resolve_module_cmd

            module_cmd = resolve_module_cmd("mining.cli.miner")
            if module_cmd is None:
                raise RuntimeError("Cannot start the miner: no way to run mining.cli.miner")

            cmd = [
                *module_cmd, "mine-blocks",
                "--address", payout_address,
                "--count", str(blocks_per_batch),
                "--threads", str(threads),
                "--rpc-url", rpc_url,
                "--allow-offline-mining",
                "--allow-unsynced"
            ]
            
            # Configurable retry delay (default: 2 seconds)
            retry_delay = config.get('miner', {}).get('retry_delay', 2.0)
            
            # Continuous mining loop - restart mine-blocks after each batch
            while not self.stop_event.is_set():
                logger.info(f"Starting miner batch: {' '.join(cmd)}")
                
                # Start the miner process
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,  # Line buffered
                    env=minimal_env
                )
                
                # Monitor output and extract events
                cycle = 0
                last_hashrate_time = time.time()
                
                while not self.stop_event.is_set():
                    if self.process.poll() is not None:
                        # Process exited - check return code
                        return_code = self.process.returncode
                        if return_code == 0:
                            logger.info(f"Miner batch completed successfully, starting next batch")
                        else:
                            logger.warning(f"Miner process exited with code {return_code}, retrying in {retry_delay}s")
                            # Wait before retrying on error, using stop_event with timeout for better responsiveness
                            self.stop_event.wait(retry_delay)
                        break
                    
                    # Read a line from output
                    try:
                        line = self.process.stdout.readline()
                        if not line:
                            time.sleep(0.1)
                            continue
                        
                        line = line.strip()
                        if not line:
                            continue
                        
                        # Emit as log
                        self._emit_event(MiningEvent(
                            event_type=EventType.LOG,
                            timestamp=time.time(),
                            data={
                                "level": "info",
                                "message": line,
                                "component": "miner"
                            }
                        ))
                        
                        # Parse mining events from output
                        line_lower = line.lower()
                        
                        # Check for block found - look for specific patterns
                        # Matches: "block", "mined", "successfully mined"
                        if re.search(r'\bblock\b.*\b(mined|found|accepted)\b', line_lower):
                            now = time.time()
                            self._last_blocks += 1
                            self._block_times.append(now)
                            
                            # Try to extract height from output
                            height_match = re.search(r'height[:\s]+(\d+)', line_lower)
                            block_height = int(height_match.group(1)) if height_match else 0
                            
                            # Calculate hashrate from block rate
                            calculated_hashrate = self._calculate_hashrate_from_blocks()
                            if calculated_hashrate > 0:
                                self._last_hashrate = calculated_hashrate
                            
                            self._emit_event(MiningEvent(
                                event_type=EventType.BLOCK_FOUND,
                                timestamp=now,
                                data={"block_count": self._last_blocks, "height": block_height}
                            ))
                            
                            # Also emit hashrate update when block is found
                            if calculated_hashrate > 0:
                                self._emit_event(MiningEvent(
                                    event_type=EventType.HASHRATE_UPDATE,
                                    timestamp=now,
                                    data={"hashrate": calculated_hashrate, "unit": "H/s"}
                                ))
                        
                        # Check for template/job updates - look for specific patterns
                        if re.search(r'\b(new\s+)?(template|job)\b', line_lower):
                            # Try to extract height and transaction count
                            height_match = re.search(r'height[:\s]+(\d+)', line_lower)
                            tx_match = re.search(r'(\d+)\s+transactions?', line_lower)
                            
                            template_height = int(height_match.group(1)) if height_match else 0
                            tx_count = int(tx_match.group(1)) if tx_match else 0
                            
                            # Try to extract theta (difficulty) - look for patterns like "theta: 12345678" or "Θ: 12345678"
                            theta_match = re.search(r'(?:theta|Θ)[:\s]+([\d]+)', line_lower)
                            if theta_match:
                                self._current_theta_micro = int(theta_match.group(1))
                            
                            self._emit_event(MiningEvent(
                                event_type=EventType.TEMPLATE_UPDATE,
                                timestamp=time.time(),
                                data={
                                    "height": template_height,
                                    "transactions": tx_count,
                                    "theta_micro": self._current_theta_micro
                                }
                            ))
                        
                        # Check for hashrate info
                        if "h/s" in line_lower or "hashrate" in line_lower:
                            # Try to extract hashrate value
                            match = re.search(r'([\d.]+)\s*(kh/s|mh/s|gh/s|h/s)', line_lower)
                            if match:
                                value = float(match.group(1))
                                unit = match.group(2)
                                
                                # Convert to H/s
                                if unit == "kh/s":
                                    hashrate = value * 1000
                                elif unit == "mh/s":
                                    hashrate = value * 1000000
                                elif unit == "gh/s":
                                    hashrate = value * 1000000000
                                else:
                                    hashrate = value
                                
                                self._last_hashrate = hashrate
                                self._emit_event(MiningEvent(
                                    event_type=EventType.HASHRATE_UPDATE,
                                    timestamp=time.time(),
                                    data={"hashrate": hashrate, "unit": "H/s"}
                                ))
                        
                        cycle += 1
                        
                        # Emit periodic hashrate updates even if not in logs
                        if time.time() - last_hashrate_time > 10.0:
                            # Recalculate hashrate from block rate
                            calculated_hashrate = self._calculate_hashrate_from_blocks()
                            if calculated_hashrate > 0:
                                self._last_hashrate = calculated_hashrate
                                self._emit_event(MiningEvent(
                                    event_type=EventType.HASHRATE_UPDATE,
                                    timestamp=time.time(),
                                    data={"hashrate": calculated_hashrate, "unit": "H/s"}
                                ))
                            last_hashrate_time = time.time()
                    
                    except Exception as e:
                        logger.error(f"Error reading miner output: {e}")
                        time.sleep(0.1)
        
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
                "blocks": self._last_blocks,
                "uptime_seconds": uptime,
                "theta_micro": self._current_theta_micro
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
