"""Local-only RPC client for communicating with the local node.

This client is restricted to localhost connections only and cannot
be used to connect to remote nodes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    import httpx
    HAVE_HTTPX = True
except ImportError:
    HAVE_HTTPX = False
    try:
        import requests
        HAVE_REQUESTS = True
    except ImportError:
        HAVE_REQUESTS = False

from .ports import validate_localhost_url
from .status import SyncStatus

logger = logging.getLogger(__name__)


class LocalRpcError(Exception):
    """RPC request error."""
    pass


class LocalRpcClient:
    """RPC client that only connects to localhost.
    
    This client enforces localhost-only connections as a security measure.
    It cannot be used to connect to remote nodes.
    """
    
    def __init__(self, port: int, auth_token: Optional[str] = None, timeout: float = 10.0):
        """Initialize local RPC client.
        
        Args:
            port: Local port number where RPC server is listening
            auth_token: Optional authentication token
            timeout: Request timeout in seconds
            
        Raises:
            ValueError: If port is invalid
        """
        if not (1 <= port <= 65535):
            raise ValueError(f"Invalid port number: {port}")
        
        self.port = port
        self.auth_token = auth_token
        self.timeout = timeout
        self._request_id = 0
        
        # Build RPC URL - always localhost
        self.rpc_url = f"http://127.0.0.1:{port}/rpc"
        
        # Validate it's actually localhost
        if not validate_localhost_url(self.rpc_url):
            raise ValueError(f"Invalid localhost URL: {self.rpc_url}")
        
        logger.debug(f"LocalRpcClient initialized: {self.rpc_url}")
    
    def _build_headers(self) -> Dict[str, str]:
        """Build request headers including auth token if present."""
        headers = {"Content-Type": "application/json"}
        
        if self.auth_token:
            headers["X-Animica-Admin-Token"] = self.auth_token
        
        return headers
    
    def _call(self, method: str, params: Optional[list] = None) -> Any:
        """Make an RPC call.
        
        Args:
            method: RPC method name
            params: Optional parameters list
        
        Returns:
            Result from RPC response
        
        Raises:
            LocalRpcError: If the request fails or returns an error
        """
        if params is None:
            params = []
        
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params
        }
        
        headers = self._build_headers()
        
        try:
            if HAVE_HTTPX:
                response = httpx.post(
                    self.rpc_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                    follow_redirects=True
                )
                response.raise_for_status()
                data = response.json()
            elif HAVE_REQUESTS:
                import requests
                response = requests.post(
                    self.rpc_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True
                )
                response.raise_for_status()
                data = response.json()
            else:
                raise LocalRpcError("No HTTP client available (install httpx or requests)")
            
            if "error" in data:
                error = data["error"]
                raise LocalRpcError(f"RPC error: {error}")
            
            return data.get("result")
        
        except Exception as e:
            if isinstance(e, LocalRpcError):
                raise
            raise LocalRpcError(f"RPC request failed: {e}")

    def call(self, method: str, params: Optional[Any] = None) -> Any:
        """Public wrapper to execute an RPC method.

        Args:
            method: RPC method name
            params: Optional params list or object
        """
        if isinstance(params, dict):
            return self._call(method, params)
        if params is None:
            return self._call(method, [])
        return self._call(method, list(params) if isinstance(params, tuple) else params)
    
    def ping(self) -> bool:
        """Check if RPC server is responsive.
        
        Returns:
            True if server responds, False otherwise
        """
        try:
            self.get_chain_head()
            return True
        except Exception as e:
            logger.debug(f"RPC ping failed: {e}")
            return False
    
    def get_chain_head(self) -> Dict[str, Any]:
        """Get current chain head.
        
        Returns:
            Dictionary with head block info
        """
        result = self._call("chain.getHead", [])
        return result or {}
    
    def get_chain_id(self) -> Optional[int]:
        """Get chain ID.
        
        Returns:
            Chain ID or None if unavailable
        """
        try:
            head = self.get_chain_head()
            return head.get("chainId") or head.get("chain_id")
        except Exception as e:
            logger.debug(f"Could not get chain ID: {e}")
            return None
    
    def get_sync_status(self) -> SyncStatus:
        """Get node sync status.
        
        Returns:
            SyncStatus object with current sync state
        """
        try:
            # Try the dedicated sync status method
            result = self._call("chain.getSyncStatus", [])
            if result:
                return SyncStatus(
                    syncing=result.get("syncing", False),
                    current_height=result.get("currentBlock", 0) or result.get("current_height", 0),
                    best_height=result.get("highestBlock", 0) or result.get("best_height", 0),
                    phase=result.get("phase", "idle"),
                    in_flight=result.get("in_flight", 0),
                    queued=result.get("queued", 0),
                    peer_count=result.get("peer_count", 0),
                    peers_in=result.get("peers_in", 0),
                    peers_out=result.get("peers_out", 0),
                    last_progress=result.get("last_progress"),
                    last_error=result.get("last_error"),
                )
        except Exception as e:
            logger.debug(f"getSyncStatus failed: {e}")
        
        # Fallback: just return head info
        try:
            head = self.get_chain_head()
            height = head.get("number", 0) or head.get("height", 0)
            return SyncStatus(
                syncing=False,
                current_height=height,
                best_height=height,
                phase="synced",
            )
        except Exception:
            return SyncStatus(syncing=False)
    
    def get_balance(self, address: str) -> Optional[int]:
        """Get balance for an address.
        
        Args:
            address: Address to query balance for
        
        Returns:
            Balance in base units (1 ANM = 1e9 base units) or None if unavailable
        """
        for method in ["state.getBalance", "state_getBalance"]:
            try:
                result = self._call(method, [address])
                if result is not None:
                    if isinstance(result, dict):
                        balance = result.get('balance') if 'balance' in result else result.get('value')
                    else:
                        balance = result
                    
                    if isinstance(balance, str):
                        if balance.startswith("0x"):
                            return int(balance, 16)
                        else:
                            return int(balance)
                    else:
                        return int(balance)
            except Exception as e:
                logger.debug(f"Method {method} failed: {e}")
                continue
        return None
    
    def get_nonce(self, address: str) -> Optional[int]:
        """Get nonce (transaction count) for an address.
        
        Args:
            address: Address to query nonce for
        
        Returns:
            Nonce (number of transactions sent) or None if unavailable
        """
        for method in ["state.getNonce", "state_getNonce"]:
            try:
                result = self._call(method, [address])
                if result is not None:
                    if isinstance(result, dict):
                        nonce = result.get('nonce') if 'nonce' in result else result
                    else:
                        nonce = result
                    
                    if isinstance(nonce, str):
                        if nonce.startswith("0x"):
                            return int(nonce, 16)
                        else:
                            return int(nonce)
                    else:
                        return int(nonce)
            except Exception as e:
                logger.debug(f"Method {method} failed: {e}")
                continue
        return None
    
    def get_block_template(self, payout_address: str) -> Dict[str, Any]:
        """Get block template for mining.
        
        Args:
            payout_address: Address to receive mining rewards
        
        Returns:
            Block template dictionary
        """
        result = self._call("mining.getTemplate", [payout_address])
        return result or {}
    
    def get_peer_count(self) -> int:
        """Get number of connected peers.
        
        Returns:
            Number of peers
        """
        try:
            result = self._call("p2p.peerCount", [])
            if isinstance(result, int):
                return result
            if isinstance(result, dict):
                return result.get("total", 0)
        except Exception:
            pass
        return 0
