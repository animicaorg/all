"""RPC client for querying chain state and templates.

Provides a simple interface to query:
- Chain head and sync status
- Mempool statistics
- Block templates for mining
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

logger = logging.getLogger(__name__)


class RPCError(Exception):
    """RPC request error."""
    pass


class RPCClient:
    """Simple RPC client for Animica node."""
    
    def __init__(self, rpc_url: str, timeout: float = 10.0):
        """Initialize RPC client.
        
        Args:
            rpc_url: RPC endpoint URL
            timeout: Request timeout in seconds
        """
        self.rpc_url = rpc_url
        self.timeout = timeout
        self._request_id = 0
    
    def _call(self, method: str, params: Optional[list] = None) -> Any:
        """Make an RPC call.
        
        Args:
            method: RPC method name
            params: Optional parameters list
        
        Returns:
            Result from RPC response
        
        Raises:
            RPCError: If the request fails or returns an error
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
        
        try:
            if HAVE_HTTPX:
                response = httpx.post(
                    self.rpc_url,
                    json=payload,
                    timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()
            elif HAVE_REQUESTS:
                import requests
                response = requests.post(
                    self.rpc_url,
                    json=payload,
                    timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()
            else:
                raise RPCError("No HTTP client available (install httpx or requests)")
            
            if "error" in data:
                error = data["error"]
                raise RPCError(f"RPC error: {error}")
            
            return data.get("result")
        
        except Exception as e:
            if isinstance(e, RPCError):
                raise
            raise RPCError(f"RPC request failed: {e}")
    
    def check_connection(self) -> bool:
        """Check if RPC connection is working.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.get_chain_head()
            return True
        except Exception as e:
            logger.debug(f"RPC connection check failed: {e}")
            return False
    
    def get_chain_head(self) -> Dict[str, Any]:
        """Get current chain head.
        
        Returns:
            Dictionary with head block info
        """
        result = self._call("chain_getHead", [])
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
    
    def get_sync_status(self) -> Dict[str, Any]:
        """Get node sync status.
        
        Returns:
            Dictionary with sync info
        """
        try:
            result = self._call("chain_getSyncStatus", [])
            return result or {}
        except Exception:
            # Fallback: just return head info
            head = self.get_chain_head()
            return {
                "syncing": False,
                "currentBlock": head.get("number", 0),
                "highestBlock": head.get("number", 0)
            }
    
    def get_mempool_stats(self) -> Dict[str, Any]:
        """Get mempool statistics.
        
        Returns:
            Dictionary with mempool stats
        """
        try:
            result = self._call("mempool_stats", [])
            return result or {"total": 0, "pending": 0}
        except Exception:
            return {"total": 0, "pending": 0}
    
    def get_block_template(self, payout_address: str) -> Dict[str, Any]:
        """Get block template for mining.
        
        Args:
            payout_address: Address to receive mining rewards
        
        Returns:
            Block template dictionary
        """
        result = self._call("mining_getTemplate", [payout_address])
        return result or {}
    
    def submit_block(self, block_data: Dict[str, Any]) -> bool:
        """Submit a mined block.
        
        Args:
            block_data: Block data to submit
        
        Returns:
            True if accepted, False otherwise
        """
        try:
            result = self._call("mining_submitBlock", [block_data])
            return result is True or result == "accepted"
        except Exception as e:
            logger.error(f"Error submitting block: {e}")
            return False
