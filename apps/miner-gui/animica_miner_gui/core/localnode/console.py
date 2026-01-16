"""Console panel support for running CLI commands against local node.

Provides infrastructure for executing CLI commands and capturing output.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .paths import is_frozen, resolve_cli_binary, get_repo_root

logger = logging.getLogger(__name__)


class ConsoleCommandExecutor:
    """Executes CLI commands against the local node."""
    
    def __init__(self, rpc_url: str, auth_token: Optional[str] = None):
        """Initialize command executor.
        
        Args:
            rpc_url: RPC URL to use for commands
            auth_token: Optional auth token
        """
        self.rpc_url = rpc_url
        self.auth_token = auth_token
    
    def _build_base_command(self) -> List[str]:
        """Build the base CLI command.
        
        Returns:
            Base command as list of arguments
        """
        cli_binary = resolve_cli_binary()
        
        if cli_binary is None:
            # Use Python module
            if is_frozen():
                raise RuntimeError("CLI not available in packaged mode")
            
            return [sys.executable, "-m", "animica"]
        else:
            return [str(cli_binary)]
    
    def _inject_rpc_args(self, args: List[str]) -> List[str]:
        """Inject RPC URL and auth token into command arguments.
        
        This ensures the command always talks to the local node.
        
        Args:
            args: Original command arguments
        
        Returns:
            Modified arguments with RPC configuration
        """
        # Remove any existing --rpc-url or --rpc-auth-token args
        cleaned_args = []
        skip_next = False
        
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            
            if arg in ("--rpc-url", "--rpc-auth-token", "--rpc-token"):
                skip_next = True
                continue
            
            if arg.startswith("--rpc-url=") or arg.startswith("--rpc-auth-token=") or arg.startswith("--rpc-token="):
                continue
            
            cleaned_args.append(arg)
        
        # Inject our RPC configuration
        result = []
        
        # Add --rpc-url right after the command
        result.extend(cleaned_args)
        result.extend(["--rpc-url", self.rpc_url])
        
        # TODO: Add auth token support if the CLI supports it
        # result.extend(["--rpc-auth-token", self.auth_token])
        
        return result
    
    def execute(self, command_line: str, timeout: float = 30.0) -> Tuple[int, str, str]:
        """Execute a CLI command.
        
        Args:
            command_line: Command line string (without 'animica' prefix)
            timeout: Command timeout in seconds
        
        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        # Parse command line
        import shlex
        try:
            args = shlex.split(command_line)
        except Exception as e:
            return (1, "", f"Failed to parse command: {e}")
        
        # Build full command
        try:
            base_cmd = self._build_base_command()
        except RuntimeError as e:
            return (1, "", str(e))
        
        # Inject RPC configuration
        full_args = self._inject_rpc_args(args)
        full_cmd = base_cmd + full_args
        
        logger.debug(f"Executing: {' '.join(full_cmd)}")
        
        # Execute command
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            
            return (result.returncode, result.stdout, result.stderr)
        
        except subprocess.TimeoutExpired:
            return (124, "", f"Command timed out after {timeout}s")
        
        except Exception as e:
            return (1, "", f"Command execution failed: {e}")
    
    @staticmethod
    def is_dangerous_command(command_line: str) -> Tuple[bool, str]:
        """Check if a command is potentially dangerous.
        
        Args:
            command_line: Command line to check
        
        Returns:
            Tuple of (is_dangerous, reason)
        """
        dangerous_keywords = [
            ("reset", "This will reset the blockchain state"),
            ("wipe", "This will delete blockchain data"),
            ("delete", "This will delete data"),
            ("rm ", "This might delete files"),
            ("remove", "This will remove data"),
        ]
        
        lower = command_line.lower()
        
        for keyword, reason in dangerous_keywords:
            if keyword in lower:
                return (True, reason)
        
        return (False, "")
