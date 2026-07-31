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

    def __init__(self, message: str, *, code: Optional[int] = None, data: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class RPCClient:
    """Simple RPC client for Animica node."""
    
    def __init__(self, rpc_url: str, timeout: float = 10.0, token: Optional[str] = None):
        """Initialize RPC client.
        
        Args:
            rpc_url: RPC endpoint URL
            timeout: Request timeout in seconds
            token: Optional admin token for Authorization header
        """
        self.rpc_url = rpc_url
        self.timeout = timeout
        self._request_id = 0
        self.token = token
    
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

        # Fail with something a user can act on. Passing None straight to httpx
        # produced "Invalid type for url. Expected str or httpx.URL, got
        # <class 'NoneType'>" in the diagnostics and logs, which tells nobody
        # that the real problem is an unconfigured endpoint.
        if not self.rpc_url:
            raise RPCError("No RPC endpoint configured")

        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params
        }
        
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-Animica-Admin-Token"] = self.token

        try:
            if HAVE_HTTPX:
                # httpx won't follow redirects by default - this causes connection failures
                # when the server issues redirects (301, 302, 307, 308, etc.). Enable explicitly.
                response = httpx.post(
                    self.rpc_url,
                    json=payload,
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
            elif HAVE_REQUESTS:
                import requests
                # requests follows redirects by default (allow_redirects=True), but made
                # explicit for consistency and to make behavior clear for future maintainers
                response = requests.post(
                    self.rpc_url,
                    json=payload,
                    timeout=self.timeout,
                    allow_redirects=True,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
            else:
                raise RPCError("No HTTP client available (install httpx or requests)")
            
            # A reachable-but-wrong URL (a web page, a proxy error page, a
            # different service) returns 200 with JSON that has no "result"
            # and no "error". Treating that as a successful None made every
            # field render "--" with no error anywhere — "connected, but no
            # stats" with nothing to debug.
            if not isinstance(data, dict) or (
                "result" not in data and "error" not in data
            ):
                raise RPCError(
                    f"{self.rpc_url} did not return a JSON-RPC response "
                    f"(got {type(data).__name__})"
                )

            if "error" in data:
                error = data["error"] or {}
                if isinstance(error, dict):
                    message = error.get("message") or str(error)
                    code = error.get("code")
                    raise RPCError(f"RPC error: {message}", code=code, data=error)
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
        return self._call_first(["chain.getHead", "chain_getHead", "chain.head", "chain_head"]) or {}
    
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
            result = self._call_first(["sync.getStatus", "sync.get_status", "sync.dump", "chain_getSyncStatus"])
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
            result = self._call_first(["mempool.stats", "mempool_stats", "mempool.stats.get"])
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
        result = self._call_first(["mining.getTemplate", "mining_getTemplate"], [payout_address])
        return result or {}
    
    def submit_block(self, block_data: Dict[str, Any]) -> bool:
        """Submit a mined block.
        
        Args:
            block_data: Block data to submit
        
        Returns:
            True if accepted, False otherwise
        """
        try:
            result = self._call_first(["mining.submitBlock", "mining_submitBlock"], [block_data])
            return result is True or result == "accepted"
        except Exception as e:
            logger.error(f"Error submitting block: {e}")
            return False
    
    def get_balance(self, address: str) -> Optional[int]:
        """Get balance for an address.
        
        Args:
            address: Address to query balance for
        
        Returns:
            Balance in base units (1 ANM = 1e9 base units) or None if unavailable
        """
        # Try multiple RPC method names for compatibility
        for method in ["state.getBalance", "state_getBalance"]:
            try:
                result = self._call(method, [address])
                if result is not None:
                    # Result might be a dict with 'balance' key or just a number
                    if isinstance(result, dict):
                        # Explicitly check for key existence to handle zero balance correctly
                        balance = result.get('balance') if 'balance' in result else result.get('value')
                    else:
                        balance = result
                    
                    # Handle hex strings and regular numbers
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
        # Try multiple RPC method names for compatibility
        for method in ["state.getNonce", "state_getNonce"]:
            try:
                result = self._call(method, [address])
                if result is not None:
                    # Result might be a dict with 'nonce' key or just a number
                    if isinstance(result, dict):
                        # Explicitly check for key existence to handle zero nonce correctly
                        nonce = result.get('nonce') if 'nonce' in result else result
                    else:
                        nonce = result
                    
                    # Handle hex strings and regular numbers
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

    def get_rpc_methods(self) -> Optional[list]:
        """Return available RPC methods, if exposed."""
        return self._call_first(["rpc.methods", "rpc_methods", "rpc.listMethods"])

    def call_contract(
        self,
        *,
        address: str,
        method: str,
        args: list,
        abi: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Call a contract method using best-effort RPC fallbacks."""
        payloads = [
            ("animica_callContract", [{"to": address, "method": method, "args": args, "abi": abi}]),
            ("state.call", [{"to": address, "method": method, "args": args, "abi": abi}]),
            ("state.call", [{"to": address, "func": method, "args": args, "abi": abi}]),
            ("contract.call", [{"to": address, "method": method, "args": args, "abi": abi}]),
            ("vm.call", [{"to": address, "method": method, "args": args, "abi": abi}]),
            ("contracts.call", [address, method, args]),
            ("eth_call", [{"to": address, "data": method, "args": args}]),
        ]
        last_exc: Optional[Exception] = None
        for rpc_method, params in payloads:
            try:
                result = self._call(rpc_method, params)
                return {"method": rpc_method, "result": result}
            except Exception as exc:
                last_exc = exc
                continue
        if last_exc:
            raise RPCError(f"All contract call RPC methods failed: {last_exc}")
        raise RPCError("No contract call RPC method succeeded.")

    def get_peer_summary(self) -> Dict[str, Any]:
        """Return peer summary information."""
        peers = self._call_first(
            [
                "p2p.listPeers",
                "p2p.getPeers",
                "p2p.peers",
                "net.peers",
                "net_peers",
            ]
        )
        if isinstance(peers, list):
            total = len(peers)
            inbound = len([p for p in peers if isinstance(p, dict) and p.get("direction") == "in"])
            outbound = len([p for p in peers if isinstance(p, dict) and p.get("direction") == "out"])
            return {"total": total, "inbound": inbound, "outbound": outbound, "peers": peers}

        counts = self._call_first(
            [
                "net.peerCount",
                "p2p.peerCount",
                "p2p.peer_count",
                "net_peerCount",
            ]
        )
        if isinstance(counts, dict):
            return counts
        if isinstance(counts, int):
            return {"total": counts, "inbound": None, "outbound": None}
        return {"total": 0, "inbound": None, "outbound": None}

    def _call_first(self, methods: list[str], params: Optional[list] = None) -> Any:
        """Try multiple RPC method names in order."""
        last_exc: Optional[Exception] = None
        for method in methods:
            try:
                return self._call(method, params)
            except Exception as exc:
                last_exc = exc
                continue
        if last_exc:
            raise last_exc
        return None
