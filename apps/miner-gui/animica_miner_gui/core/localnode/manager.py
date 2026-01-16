"""Local node manager - main interface for controlling the local node.

This is the primary interface that the GUI uses to manage the local node.
"""

from __future__ import annotations

import ast
import json
import logging
import time
from typing import Optional

from .proc import NodeProcessManager
from .rpc import LocalRpcClient, LocalRpcError
from .status import NodeState, NodeStatus, SyncStatus

logger = logging.getLogger(__name__)

DEFAULT_READY_TIMEOUT = 30.0
READY_CHECK_INTERVAL = 0.5


class LocalNodeManager:
    """High-level manager for the local node.
    
    This class provides a simple interface for:
    - Starting and stopping the node
    - Checking node status and readiness
    - Getting an RPC client for the node
    - Monitoring sync status
    """
    
    def __init__(self, network: str = "devnet", preferred_port: Optional[int] = None):
        """Initialize local node manager.
        
        Args:
            network: Network name (mainnet, testnet, devnet)
            preferred_port: Preferred RPC port, or None for default
        """
        self.network = network
        self.proc_manager = NodeProcessManager(network, preferred_port)
        self._rpc_client: Optional[LocalRpcClient] = None
        self._status = NodeStatus(state=NodeState.STOPPED)
        self._rpc_url: Optional[str] = None
        self._auth_token: Optional[str] = None
    
    def start(self, ready_timeout: float = DEFAULT_READY_TIMEOUT) -> NodeStatus:
        """Start the local node and wait for it to be ready.
        
        Args:
            ready_timeout: Maximum time to wait for node to be ready
        
        Returns:
            Node status after startup
        """
        if self._status.is_running:
            logger.warning("Node is already running")
            return self._status
        
        # Start the process
        self._status = self.proc_manager.start()
        self._rpc_client = None
        self._rpc_url = self.rpc_url
        self._auth_token = self.proc_manager.auth_token
        
        if self._status.state == NodeState.ERROR:
            return self._status
        
        # Wait for readiness
        logger.info(f"Waiting for node to be ready (timeout: {ready_timeout}s)...")
        start_time = time.time()
        
        while time.time() - start_time < ready_timeout:
            # Check if process is still running
            if not self.proc_manager.is_running():
                elapsed = time.time() - start_time
                exit_code = self.proc_manager.last_exit_code
                reason = "Node process exited unexpectedly"
                if elapsed <= 5.0:
                    reason = f"Node exited within {elapsed:.1f}s"
                self.proc_manager.record_failure(reason=reason, exit_code=exit_code)
                self._status = NodeStatus(
                    state=NodeState.ERROR,
                    error=reason
                )
                return self._status
            
            # Try to create RPC client and ping
            try:
                if self._rpc_client is None:
                    self._rpc_client = self._build_rpc_client()

                if self._rpc_client.ping():
                    # Node is ready!
                    self._status = NodeStatus(
                        state=NodeState.READY,
                        pid=self.proc_manager.process.pid if self.proc_manager.process else None,
                        port=self.proc_manager.port,
                    )
                    logger.info("Node is ready")
                    return self._status
            
            except LocalRpcError as e:
                if _rpc_error_code(e) == -32001:
                    error_msg = "RPC unauthorized: auth token rejected by node"
                    logger.error(error_msg)
                    self.proc_manager.record_failure(reason=error_msg, exit_code=self.proc_manager.last_exit_code)
                    self._status = NodeStatus(
                        state=NodeState.ERROR,
                        error=error_msg,
                    )
                    self.stop()
                    return self._status
                logger.debug(f"Readiness check failed: {e}")
            except Exception as e:
                logger.debug(f"Readiness check failed: {e}")
            
            # Wait before retry
            time.sleep(READY_CHECK_INTERVAL)
        
        # Timeout
        logger.error("Node readiness timeout")
        self.proc_manager.record_failure(
            reason=f"Node failed to become ready within {ready_timeout}s",
            exit_code=self.proc_manager.last_exit_code,
        )
        self._status = NodeStatus(
            state=NodeState.ERROR,
            pid=self.proc_manager.process.pid if self.proc_manager.process else None,
            port=self.proc_manager.port,
            error=f"Node failed to become ready within {ready_timeout}s"
        )
        
        # Stop the process since it's not ready
        self.stop()
        
        return self._status
    
    def stop(self) -> NodeStatus:
        """Stop the local node.
        
        Returns:
            Node status after stopping
        """
        self._status = self.proc_manager.stop()
        self._rpc_client = None
        self._rpc_url = None
        self._auth_token = None
        return self._status
    
    def restart(self, ready_timeout: float = DEFAULT_READY_TIMEOUT) -> NodeStatus:
        """Restart the local node.
        
        Args:
            ready_timeout: Maximum time to wait for node to be ready
        
        Returns:
            Node status after restart
        """
        logger.info("Restarting node...")
        self.stop()
        time.sleep(1.0)  # Brief pause
        return self.start(ready_timeout)
    
    def get_status(self) -> NodeStatus:
        """Get current node status.
        
        Returns:
            Current node status
        """
        # Update process status
        if self._status.is_running:
            if not self.proc_manager.is_running():
                # Process died
                self.proc_manager.record_failure(
                    reason="Node process exited unexpectedly",
                    exit_code=self.proc_manager.last_exit_code,
                )
                self._status = NodeStatus(
                    state=NodeState.ERROR,
                    error="Node process exited unexpectedly"
                )
            else:
                # Update uptime
                proc_status = self.proc_manager.get_status()
                if self._status.state == NodeState.READY:
                    self._status = NodeStatus(
                        state=NodeState.READY,
                        pid=proc_status.pid,
                        port=proc_status.port,
                        uptime_seconds=proc_status.uptime_seconds,
                    )
        
        return self._status
    
    def get_rpc_client(self) -> Optional[LocalRpcClient]:
        """Get RPC client for the local node.
        
        Returns:
            RPC client, or None if node is not ready
        """
        if not self._status.is_ready:
            return None
        if self._rpc_client is None:
            self._rpc_client = self._build_rpc_client()
        else:
            current_url = self.rpc_url
            current_token = self.proc_manager.auth_token
            if current_url != self._rpc_client.rpc_url or current_token != self._rpc_client.auth_token:
                self._rpc_client = self._build_rpc_client()
        self._rpc_url = self.rpc_url
        self._auth_token = self.proc_manager.auth_token
        return self._rpc_client
    
    def get_sync_status(self) -> Optional[SyncStatus]:
        """Get blockchain sync status.
        
        Returns:
            Sync status, or None if node is not ready
        """
        client = self.get_rpc_client()
        if client is None:
            return None
        
        try:
            return client.get_sync_status()
        except LocalRpcError as e:
            logger.debug(f"Failed to get sync status: {e}")
            return None
    
    @property
    def is_running(self) -> bool:
        """Check if node is running."""
        return self._status.is_running
    
    @property
    def is_ready(self) -> bool:
        """Check if node is ready for RPC calls."""
        return self._status.is_ready
    
    @property
    def port(self) -> Optional[int]:
        """Get the RPC port."""
        return self._status.port
    
    @property
    def rpc_url(self) -> Optional[str]:
        """Get the RPC URL.
        
        Returns:
            RPC URL (always localhost), or None if not running
        """
        if self.port is None:
            return None
        return f"http://127.0.0.1:{self.port}/rpc"

    @property
    def auth_token(self) -> Optional[str]:
        """Get the current RPC auth token."""
        return self._auth_token

    def _build_rpc_client(self) -> LocalRpcClient:
        return LocalRpcClient(
            port=self.proc_manager.port,
            auth_token=self.proc_manager.auth_token,
            auth_token_path=self.proc_manager.token_path,
        )


def _rpc_error_code(exc: Exception) -> Optional[int]:
    if not isinstance(exc, LocalRpcError):
        return None
    message = str(exc)
    if not message.startswith("RPC error:"):
        return None
    payload = message.replace("RPC error:", "", 1).strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        try:
            data = ast.literal_eval(payload)
        except Exception:
            return None
    if isinstance(data, dict):
        code = data.get("code")
        return int(code) if isinstance(code, (int, float)) else None
    return None
