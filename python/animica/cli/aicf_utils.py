"""
AICF CLI Utilities
===================

Production-ready utilities for AICF CLI commands including:
- RPC URL normalization and validation
- Safe JSON encoding for BigInt
- Retry logic with exponential backoff
- Error classification and user-friendly messages
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("animica.cli.aicf")


def normalize_rpc_url(url: str) -> str:
    """
    Normalize RPC URL to ensure it ends with /rpc.
    
    Examples:
        http://127.0.0.1:8545 -> http://127.0.0.1:8545/rpc
        http://127.0.0.1:8545/ -> http://127.0.0.1:8545/rpc
        http://127.0.0.1:8545/rpc -> http://127.0.0.1:8545/rpc (unchanged)
        http://127.0.0.1:8545/rpc/ -> http://127.0.0.1:8545/rpc
    
    Args:
        url: Base RPC URL
        
    Returns:
        Normalized URL with /rpc path
    """
    if not url:
        return "http://127.0.0.1:8545/rpc"
    
    # Ensure URL has scheme
    if "://" not in url:
        url = f"http://{url}"
    
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    
    # If path already ends with /rpc, keep it
    if path.endswith("/rpc"):
        return urlunparse(parsed._replace(path=path))
    
    # Otherwise, append /rpc
    if path and path != "/":
        # Path exists but doesn't end with /rpc - replace it
        # This handles cases like /api or /v1 incorrectly set
        path = "/rpc"
    else:
        path = "/rpc"
    
    return urlunparse(parsed._replace(path=path))


def get_rpc_url(override: Optional[str] = None, debug: bool = False) -> str:
    """
    Get RPC URL with proper normalization.
    
    Priority order:
    1. override parameter
    2. ANIMICA_RPC_URL environment variable
    3. Default: http://127.0.0.1:8545/rpc
    
    Args:
        override: Optional URL override
        debug: If True, print the resolved URL
        
    Returns:
        Normalized RPC URL
    """
    url = override or os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    normalized = normalize_rpc_url(url)
    
    if debug:
        if url != normalized:
            log.info(f"RPC URL normalized: {url} -> {normalized}")
        else:
            log.info(f"RPC URL: {normalized}")
    
    return normalized


class _SafeEncoder(json.JSONEncoder):
    """JSON encoder that converts large ints to strings and bytes to hex."""

    def encode(self, o: Any) -> str:
        return super().encode(self._coerce(o))

    def iterencode(self, o: Any, _one_shot: bool = False):  # type: ignore[override]
        return super().iterencode(self._coerce(o), _one_shot)

    @classmethod
    def _coerce(cls, o: Any) -> Any:
        if isinstance(o, bool):
            return o
        if isinstance(o, int) and (o >= 2**53 or o <= -(2**53)):
            return str(o)
        if isinstance(o, bytes):
            return "0x" + o.hex()
        if isinstance(o, dict):
            return {k: cls._coerce(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [cls._coerce(v) for v in o]
        return o


def safe_json_encode(obj: Any) -> str:
    """
    Safely encode objects to JSON, handling BigInt and other non-standard types.

    Large integers (>= 2^53 or <= -2^53) are serialized as strings to preserve
    precision in environments that use IEEE-754 doubles (JavaScript, JSON parsers).
    Bytes values are encoded as ``0x``-prefixed hex strings.

    Args:
        obj: Object to encode

    Returns:
        JSON string
    """
    return json.dumps(obj, cls=_SafeEncoder, indent=2)


def create_rpc_session(timeout: int = 30, retries: int = 3) -> requests.Session:
    """
    Create a requests session with retry logic and timeout.
    
    Args:
        timeout: Request timeout in seconds
        retries: Maximum number of retries for transient failures
        
    Returns:
        Configured requests.Session
    """
    session = requests.Session()
    
    # Configure retry strategy for transient network errors
    retry_strategy = Retry(
        total=retries,
        backoff_factor=0.5,  # 0.5s, 1s, 2s delays
        status_forcelist=[429, 500, 502, 503, 504],  # Retry on these HTTP status codes
        allowed_methods=["POST"],  # Only retry POST (JSON-RPC)
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session


def rpc_call(
    method: str,
    params: Optional[list] = None,
    url: Optional[str] = None,
    timeout: int = 30,
    debug: bool = False,
) -> Any:
    """
    Make a JSON-RPC call with production-ready error handling.
    
    Args:
        method: JSON-RPC method name
        params: Method parameters (optional)
        url: RPC URL override (optional)
        timeout: Request timeout in seconds
        debug: Enable debug output
        
    Returns:
        RPC result
        
    Raises:
        Exception with user-friendly error message
    """
    rpc_url = get_rpc_url(url, debug=debug)
    
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or [],
        "id": 1,
    }
    
    if debug:
        log.info(f"RPC Request: {method}")
        log.info(f"URL: {rpc_url}")
        log.info(f"Params: {params}")
    
    try:
        session = create_rpc_session(timeout=timeout)
        resp = session.post(rpc_url, json=payload, timeout=timeout)
        
        if debug:
            log.info(f"Response status: {resp.status_code}")
            log.info(f"Response headers: {dict(resp.headers)}")
        
        # Handle 405 Method Not Allowed specifically
        if resp.status_code == 405:
            raise Exception(
                f"❌ 405 Method Not Allowed\n\n"
                f"Your RPC URL is incorrect or missing /rpc:\n"
                f"  Current: {rpc_url}\n\n"
                f"The RPC server expects POST requests to /rpc.\n"
                f"Fix: Set ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc\n"
            )
        
        # Handle other HTTP errors
        resp.raise_for_status()
        
        # Try to parse JSON response
        try:
            data = resp.json()
        except ValueError as e:
            # Server returned non-JSON (might be HTML error page)
            content_preview = resp.text[:200]
            raise Exception(
                f"❌ Server returned non-JSON response\n\n"
                f"URL: {rpc_url}\n"
                f"Status: {resp.status_code}\n"
                f"Content preview: {content_preview}\n\n"
                f"This might indicate:\n"
                f"  - Wrong endpoint (not an RPC server)\n"
                f"  - Server is down or misconfigured\n"
                f"  - Network proxy interference\n"
            )
        
        # Check for JSON-RPC error
        if "error" in data:
            error = data["error"]
            code = error.get("code", -32000)
            message = error.get("message", "Unknown error")
            detail = error.get("data", "")
            
            # Classify errors for better UX
            if code == -32601:
                raise Exception(
                    f"❌ Method not found: {method}\n\n"
                    f"The RPC server does not support this method.\n"
                    f"Try: animica rpc call rpc.discover\n"
                )
            elif code == -32602:
                raise Exception(
                    f"❌ Invalid params for {method}\n\n"
                    f"Error: {message}\n"
                    f"Params: {params}\n"
                )
            else:
                error_msg = f"RPC error: {message}"
                if detail:
                    error_msg += f"\nDetails: {detail}"
                raise Exception(error_msg)
        
        return data.get("result")
        
    except requests.exceptions.Timeout:
        raise Exception(
            f"❌ Request timeout\n\n"
            f"URL: {rpc_url}\n"
            f"The server did not respond within {timeout} seconds.\n"
            f"Check that the node is running and accessible.\n"
        )
    except requests.exceptions.ConnectionError as e:
        raise Exception(
            f"❌ Connection failed\n\n"
            f"URL: {rpc_url}\n"
            f"Could not connect to the RPC server.\n\n"
            f"Troubleshooting:\n"
            f"  1. Check the node is running: animica node status\n"
            f"  2. Verify the URL is correct\n"
            f"  3. Check firewall/network settings\n\n"
            f"Technical details: {str(e)}\n"
        )
    except requests.exceptions.RequestException as e:
        raise Exception(f"RPC request failed: {e}")


def rpc_doctor(url: Optional[str] = None) -> dict:
    """
    Diagnose RPC connectivity and return health information.
    
    Tries multiple discovery methods:
    1. rpc.discover (lists available methods)
    2. node.ping (basic connectivity)
    3. chain.getChainId (fallback method)
    
    Args:
        url: RPC URL to diagnose
        
    Returns:
        Dict with diagnosis results
    """
    rpc_url = get_rpc_url(url)
    results = {
        "url": rpc_url,
        "reachable": False,
        "methods": [],
        "errors": [],
    }
    
    # Try rpc.discover
    try:
        discover_result = rpc_call("rpc.discover", url=url, timeout=10)
        results["reachable"] = True
        if isinstance(discover_result, dict) and "methods" in discover_result:
            results["methods"] = discover_result["methods"]
        return results
    except Exception as e:
        results["errors"].append(f"rpc.discover failed: {str(e)}")
    
    # Try node.ping
    try:
        ping_result = rpc_call("node.ping", url=url, timeout=10)
        results["reachable"] = True
        results["ping"] = ping_result
        return results
    except Exception as e:
        results["errors"].append(f"node.ping failed: {str(e)}")
    
    # Try chain.getChainId as last resort
    try:
        chain_id = rpc_call("chain.getChainId", url=url, timeout=10)
        results["reachable"] = True
        results["chain_id"] = chain_id
        return results
    except Exception as e:
        results["errors"].append(f"chain.getChainId failed: {str(e)}")
    
    return results


__all__ = [
    "normalize_rpc_url",
    "get_rpc_url",
    "safe_json_encode",
    "create_rpc_session",
    "rpc_call",
    "rpc_doctor",
]
