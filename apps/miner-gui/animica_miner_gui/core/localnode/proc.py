"""Process management for local node.

Handles starting, stopping, and monitoring the node process.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

from .paths import (
    is_frozen,
    resolve_node_binary,
    get_data_directory,
    get_log_directory,
    get_rpc_token_path,
    NodeBundleError,
)
from .ports import get_rpc_port
from .status import NodeState, NodeStatus

logger = logging.getLogger(__name__)

TAIL_LINES = 200


@dataclass
class NodeFailureDetails:
    reason: str
    exit_code: Optional[int]
    stdout_tail: str
    stderr_tail: str
    stdout_log: Optional[Path]
    stderr_log: Optional[Path]
    argv: List[str]
    cwd: str
    env_deltas: Dict[str, str]
    node_path: str


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
        self.last_exit_code: Optional[int] = None
        self.stdout_log_path: Optional[Path] = None
        self.stderr_log_path: Optional[Path] = None
        self._stdout_handle: Optional[object] = None
        self._stderr_handle: Optional[object] = None
        self._last_failure: Optional[NodeFailureDetails] = None
        self._last_argv: List[str] = []
        self._last_env_deltas: Dict[str, str] = {}
        self._last_cwd: str = ""
        self._last_node_path: str = ""
        
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
    
    def _build_node_command(self, port: int) -> tuple[List[str], Dict[str, str], Path, Optional[Path]]:
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
        
        return cmd, env_vars, log_file, node_binary

    def _read_tail(self, path: Optional[Path], max_lines: int = TAIL_LINES) -> str:
        if path is None or not path.exists():
            return ""
        try:
            content = path.read_text(errors="ignore")
        except Exception as exc:
            return f"(failed to read {path}: {exc})"
        lines = content.splitlines()
        if not lines:
            return "(no output)"
        return "\n".join(lines[-max_lines:])

    def _record_failure(
        self,
        reason: str,
        exit_code: Optional[int] = None,
        stdout_tail: Optional[str] = None,
        stderr_tail: Optional[str] = None,
    ) -> None:
        stdout_text = stdout_tail if stdout_tail is not None else self._read_tail(self.stdout_log_path)
        stderr_text = stderr_tail if stderr_tail is not None else self._read_tail(self.stderr_log_path)
        self._last_failure = NodeFailureDetails(
            reason=reason,
            exit_code=exit_code,
            stdout_tail=stdout_text,
            stderr_tail=stderr_text,
            stdout_log=self.stdout_log_path,
            stderr_log=self.stderr_log_path,
            argv=self._last_argv,
            cwd=self._last_cwd,
            env_deltas=self._last_env_deltas,
            node_path=self._last_node_path,
        )
        if exit_code is not None:
            logger.info(f"Node exit code: {exit_code}")

    def _close_log_handles(self) -> None:
        if self._stdout_handle not in (None, subprocess.DEVNULL):
            try:
                self._stdout_handle.close()
            except Exception:
                pass
        if self._stderr_handle not in (None, subprocess.DEVNULL):
            try:
                self._stderr_handle.close()
            except Exception:
                pass

    def pop_last_failure_details(self) -> Optional[NodeFailureDetails]:
        details = self._last_failure
        self._last_failure = None
        return details

    def record_failure(self, reason: str, exit_code: Optional[int] = None) -> None:
        self._record_failure(reason=reason, exit_code=exit_code)

    def _preflight_node(self, node_binary: Path, env: Dict[str, str], cwd: str) -> Optional[str]:
        try:
            result = subprocess.run(
                [str(node_binary), "--help"],
                env=env,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            error_message = f"Node bundle is broken: failed to run {node_binary} --help ({exc})"
            self._record_failure(reason="Node preflight failed", stdout_tail="", stderr_tail=str(exc))
            return error_message

        if result.returncode != 0:
            stderr_snippet = (result.stderr or result.stdout).strip()
            if stderr_snippet:
                stderr_lines = "\n".join(stderr_snippet.splitlines()[-20:])
            else:
                stderr_lines = "(no output)"
            self._record_failure(
                reason="Node preflight failed",
                exit_code=result.returncode,
                stdout_tail=result.stdout.strip() or "(no output)",
                stderr_tail=stderr_lines,
            )
            return (
                "Node bundle is broken. "
                f"Preflight failed for {node_binary}.\n"
                f"{stderr_lines}\n"
                "Please reinstall the app or rebuild the node bundle."
            )

        return None
    
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
            cmd, env_vars, log_file, node_binary = self._build_node_command(self.port)
        except (FileNotFoundError, NodeBundleError) as e:
            self._record_failure(reason=str(e))
            return NodeStatus(
                state=NodeState.ERROR,
                error=str(e)
            )
        
        # Prepare environment
        env = os.environ.copy()
        env.update(env_vars)
        cwd = os.getcwd()

        self._last_argv = cmd
        self._last_env_deltas = env_vars
        self._last_cwd = cwd
        self._last_node_path = str(node_binary) if node_binary else cmd[0]

        node_log_dir = self.log_dir / "node"
        node_log_dir.mkdir(parents=True, exist_ok=True)
        self.stdout_log_path = node_log_dir / "node-stdout.log"
        self.stderr_log_path = node_log_dir / "node-stderr.log"

        logger.info(f"NODE_PATH={self._last_node_path}")
        logger.info(f"ARGV={cmd}")
        logger.info(f"CWD={cwd}")
        logger.info(f"ENV_DELTAS={env_vars}")
        
        # Ensure log file directory exists for node-side logging
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to prepare log directory: {e}")

        try:
            self._stdout_handle = open(self.stdout_log_path, "a")
            self._stderr_handle = open(self.stderr_log_path, "a")
        except Exception as e:
            logger.error(f"Failed to open stdout/stderr log files: {e}")
            self._stdout_handle = subprocess.DEVNULL
            self._stderr_handle = subprocess.DEVNULL

        if node_binary is not None:
            preflight_error = self._preflight_node(node_binary, env, cwd)
            if preflight_error:
                self._close_log_handles()
                return NodeStatus(
                    state=NodeState.ERROR,
                    error=preflight_error,
                )
        
        # Start process
        try:
            logger.info(f"Starting node: {' '.join(cmd)}")
            logger.debug(f"Environment: {env_vars}")
            logger.debug(f"Log file: {log_file}")
            
            self.process = subprocess.Popen(
                cmd,
                env=env,
                stdout=self._stdout_handle,
                stderr=self._stderr_handle,
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
            self._record_failure(reason=f"Failed to start node: {e}")
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
            self._close_log_handles()
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
            logger.info(f"Node process exited with code {poll}")
            self.last_exit_code = poll
            self._close_log_handles()
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
