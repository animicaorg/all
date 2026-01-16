"""Process management for local node.

Handles starting, stopping, and monitoring the node process.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from .paths import (
    is_frozen,
    resolve_node_binary,
    get_data_directory,
    get_log_directory,
    get_rpc_token_path,
)
from .ports import get_rpc_port
from .status import NodeState, NodeStatus

logger = logging.getLogger(__name__)


class NodeProcessManager:
    """Manages the local node process lifecycle."""
    
    def __init__(self, network: str = "devnet", preferred_port: Optional[int] = None):
        """Initialize node process manager.
        
        Args:
            network: Network name (mainnet, testnet, devnet)
            preferred_port: Preferred RPC port, or None for default
        """
        self.network = network
        self.preferred_port = preferred_port
        self.process: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None
        self.auth_token: Optional[str] = None
        self.start_time: Optional[datetime] = None
        
        # Paths
        self.data_dir = get_data_directory(network)
        self.log_dir = get_log_directory()
        self.token_path = get_rpc_token_path()
    
    def _generate_auth_token(self) -> str:
        """Generate a random auth token.
        
        Returns:
            Hex-encoded random token
        """
        import secrets
        return secrets.token_hex(32)
    
    def _prepare_auth_token(self) -> str:
        """Prepare auth token for RPC authentication.
        
        Creates or loads the token file.
        
        Returns:
            Auth token string
        """
        # Try to load existing token
        if self.token_path.exists():
            try:
                token = self.token_path.read_text().strip()
                if token:
                    logger.debug("Loaded existing RPC auth token")
                    return token
            except Exception as e:
                logger.warning(f"Failed to load auth token: {e}")
        
        # Generate new token
        token = self._generate_auth_token()
        
        # Save token
        try:
            self.token_path.write_text(token)
            self.token_path.chmod(0o600)  # Secure permissions
            logger.debug("Generated new RPC auth token")
        except Exception as e:
            logger.error(f"Failed to save auth token: {e}")
        
        return token
    
    def _build_node_command(self, port: int) -> List[str]:
        """Build the command line to start the node.
        
        Args:
            port: RPC port to use
        
        Returns:
            Command line as list of arguments
            
        Raises:
            FileNotFoundError: If node binary cannot be resolved
        """
        node_binary = resolve_node_binary()
        
        # Determine if we're using Python module or binary
        if node_binary is None:
            # Use Python module (dev mode)
            if is_frozen():
                raise RuntimeError("Cannot use Python module in frozen mode")
            
            cmd = [sys.executable, "-m", "rpc.server"]
        else:
            # Use binary
            cmd = [str(node_binary)]
        
        # Log file
        log_file = self.log_dir / f"node-{self.network}.log"
        
        cmd.extend([
            "--rpc-bind", "127.0.0.1",
            "--rpc-port", str(port),
            "--rpc-auth-token-file", str(self.token_path),
            "--data-dir", str(self.data_dir.parent),
            "--log-file", str(log_file),
        ])
        
        # Environment variables for node configuration
        env_vars = {
            "ANIMICA_NETWORK": self.network,
            "ANIMICA_DATA_DIR": str(self.data_dir.parent),  # Base .animica directory
            "ANIMICA_LOG_LEVEL": "INFO",
        }
        
        return cmd, env_vars, log_file
    
    def start(self) -> NodeStatus:
        """Start the local node process.
        
        Returns:
            Initial node status
            
        Raises:
            RuntimeError: If node is already running or fails to start
        """
        if self.is_running():
            raise RuntimeError("Node is already running")
        
        # Get available port
        self.port = get_rpc_port(self.preferred_port)
        logger.info(f"Starting node on port {self.port}")
        
        # Prepare auth token
        self.auth_token = self._prepare_auth_token()
        
        # Build command
        try:
            cmd, env_vars, log_file = self._build_node_command(self.port)
        except FileNotFoundError as e:
            return NodeStatus(
                state=NodeState.ERROR,
                error=str(e)
            )
        
        # Prepare environment
        env = os.environ.copy()
        env.update(env_vars)
        
        # Open log file
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(log_file, 'a')
        except Exception as e:
            logger.error(f"Failed to open log file: {e}")
            log_handle = subprocess.DEVNULL
        
        # Start process
        try:
            logger.info(f"Starting node: {' '.join(cmd)}")
            logger.debug(f"Environment: {env_vars}")
            logger.debug(f"Log file: {log_file}")
            
            self.process = subprocess.Popen(
                cmd,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # Detach from parent
            )
            
            self.start_time = datetime.now()
            
            logger.info(f"Node process started with PID {self.process.pid}")
            
            return NodeStatus(
                state=NodeState.STARTING,
                pid=self.process.pid,
                port=self.port,
            )
        
        except Exception as e:
            logger.error(f"Failed to start node: {e}")
            return NodeStatus(
                state=NodeState.ERROR,
                error=f"Failed to start node: {e}"
            )
    
    def stop(self, timeout: float = 10.0) -> NodeStatus:
        """Stop the local node process.
        
        Args:
            timeout: Maximum time to wait for graceful shutdown
        
        Returns:
            Final node status
        """
        if not self.is_running():
            return NodeStatus(state=NodeState.STOPPED)
        
        logger.info("Stopping node...")
        
        try:
            # Try graceful termination
            self.process.terminate()
            
            try:
                self.process.wait(timeout=timeout)
                logger.info("Node stopped gracefully")
            except subprocess.TimeoutExpired:
                logger.warning("Node did not stop gracefully, killing...")
                self.process.kill()
                self.process.wait(timeout=5.0)
                logger.info("Node killed")
        
        except Exception as e:
            logger.error(f"Error stopping node: {e}")
        
        finally:
            self.process = None
            self.port = None
            self.start_time = None
        
        return NodeStatus(state=NodeState.STOPPED)
    
    def is_running(self) -> bool:
        """Check if node process is running.
        
        Returns:
            True if running, False otherwise
        """
        if self.process is None:
            return False
        
        # Check if process is still alive
        poll = self.process.poll()
        if poll is not None:
            # Process has exited
            logger.debug(f"Node process exited with code {poll}")
            self.process = None
            self.port = None
            self.start_time = None
            return False
        
        return True
    
    def get_status(self) -> NodeStatus:
        """Get current node status.
        
        Returns:
            Current node status
        """
        if not self.is_running():
            return NodeStatus(state=NodeState.STOPPED)
        
        uptime = 0.0
        if self.start_time:
            uptime = (datetime.now() - self.start_time).total_seconds()
        
        return NodeStatus(
            state=NodeState.STARTING,  # Will be updated to READY after readiness check
            pid=self.process.pid if self.process else None,
            port=self.port,
            uptime_seconds=uptime,
        )
    
    def get_log_file(self) -> Path:
        """Get the path to the node log file.
        
        Returns:
            Path to log file
        """
        return self.log_dir / f"node-{self.network}.log"
