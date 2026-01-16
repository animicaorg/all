"""Port management for local node RPC server.

Handles finding available ports and managing port allocation.
"""

from __future__ import annotations

import logging
import socket
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_RPC_PORT = 8545
PORT_SCAN_START = 8545
PORT_SCAN_END = 8595


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a port is available for binding.
    
    Args:
        port: Port number to check
        host: Host address to check (default: 127.0.0.1)
    
    Returns:
        True if port is available, False otherwise
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return True
    except OSError:
        return False


def find_available_port(start: int = PORT_SCAN_START, end: int = PORT_SCAN_END) -> Optional[int]:
    """Find an available port in the given range.
    
    Args:
        start: Start of port range (inclusive)
        end: End of port range (inclusive)
    
    Returns:
        Available port number, or None if no ports available
    """
    for port in range(start, end + 1):
        if is_port_available(port):
            logger.debug(f"Found available port: {port}")
            return port
    
    logger.warning(f"No available ports found in range {start}-{end}")
    return None


def get_rpc_port(preferred_port: Optional[int] = None) -> int:
    """Get an available RPC port.
    
    Args:
        preferred_port: Preferred port number, or None to use default
    
    Returns:
        Available port number
        
    Raises:
        RuntimeError: If no available ports can be found
    """
    # Try preferred port first
    if preferred_port is not None:
        if is_port_available(preferred_port):
            logger.info(f"Using preferred RPC port: {preferred_port}")
            return preferred_port
        else:
            logger.warning(f"Preferred port {preferred_port} not available, scanning for alternative")
    
    # Try default port
    if is_port_available(DEFAULT_RPC_PORT):
        logger.info(f"Using default RPC port: {DEFAULT_RPC_PORT}")
        return DEFAULT_RPC_PORT
    
    # Scan for available port
    port = find_available_port()
    if port is None:
        raise RuntimeError(f"No available ports found in range {PORT_SCAN_START}-{PORT_SCAN_END}")
    
    logger.info(f"Using scanned RPC port: {port}")
    return port


def validate_localhost_url(url: str) -> bool:
    """Validate that a URL is localhost-only.
    
    This is a security guard to ensure the GUI never connects to remote nodes.
    
    Args:
        url: URL to validate
    
    Returns:
        True if URL is localhost, False otherwise
    """
    url_lower = url.lower()
    
    # Must be HTTP (not HTTPS) and localhost/127.0.0.1
    if not url_lower.startswith("http://"):
        return False
    
    # Extract host from URL
    # Format: http://host:port/path
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname
        
        # Must be localhost or 127.0.0.1
        if host not in ("localhost", "127.0.0.1"):
            return False
        
        return True
    except Exception:
        return False
