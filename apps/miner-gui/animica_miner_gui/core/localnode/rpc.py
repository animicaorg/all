"""Local-only RPC client for communicating with the local node.

This client is restricted to localhost connections only and cannot
be used to connect to remote nodes.
"""

from __future__ import annotations

import ast
import json
import logging
import time
from pathlib import Path
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
    
    def __init__(
        self,
        port: int,
        auth_token: Optional[str] = None,
        auth_token_path: Optional[Path] = None,
        timeout: float = 10.0,
    ):
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
        self.auth_token_path = auth_token_path
        self.timeout = timeout
        self._request_id = 0
        self._methods_cache: Optional[set[str]] = None
        self._methods_cache_expires_at = 0.0
        self._methods_cache_source: Optional[str] = None
        
        # Build RPC URL - always localhost
        self.rpc_url = f"http://127.0.0.1:{port}/rpc"
        
        # Validate it's actually localhost
        if not validate_localhost_url(self.rpc_url):
            raise ValueError(f"Invalid localhost URL: {self.rpc_url}")
        
        logger.debug(f"LocalRpcClient initialized: {self.rpc_url}")

    def ensure_methods(self) -> set[str]:
        """Ensure the supported RPC methods list is populated and fresh."""
        now = time.time()
        if self._methods_cache and now < self._methods_cache_expires_at:
            return self._methods_cache

        methods = self.discover_methods()
        self._methods_cache = methods
        self._methods_cache_expires_at = now + 60.0
        logger.info(
            "RPC method discovery (%s): %s",
            self.rpc_url,
            ", ".join(sorted(methods)) if methods else "(none)",
        )
        return methods

    def supports(self, method: str) -> bool:
        """Return True if a method is supported based on cached discovery."""
        return method in self.ensure_methods()

    def discover_methods(self) -> set[str]:
        """Discover supported RPC methods via discovery calls or probing."""
        discovery_methods = ["rpc.discover", "rpc.methods", "rpc.listMethods", "rpc.help"]
        for discovery in discovery_methods:
            try:
                result = self._call(discovery, [])
            except Exception as exc:
                if _rpc_error_code(exc) == -32601:
                    continue
                logger.debug("RPC discovery call %s failed: %s", discovery, exc)
                continue

            methods = _extract_methods(result)
            if methods:
                self._methods_cache_source = "discovery"
                return methods

        self._methods_cache_source = "probe"
        return self._probe_methods()

    def _probe_methods(self) -> set[str]:
        probe_methods = [
            "chain.getHead",
            "sync.getStatus",
            "sync.dump",
            "net.peers",
            "net.getPeers",
            "peer.list",
            "p2p.peers",
            "chain.getHeight",
            "chain.getTip",
            "state.getBalance",
        ]
        supported: set[str] = set()
        for method in probe_methods:
            try:
                self._call(method, [])
            except Exception as exc:
                if _rpc_error_code(exc) == -32601:
                    continue
                supported.add(method)
                continue
            supported.add(method)
        return supported
    
    def _read_auth_token(self) -> Optional[str]:
        if self.auth_token_path is None:
            return self.auth_token
        try:
            token = self.auth_token_path.read_text().strip()
            if token:
                self.auth_token = token
                return token
        except Exception as exc:
            logger.debug("Failed to read auth token file %s: %s", self.auth_token_path, exc)
        return self.auth_token

    def _build_headers(self) -> Dict[str, str]:
        """Build request headers including auth token if present."""
        headers = {"Content-Type": "application/json"}

        token = self._read_auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Animica-Admin-Token"] = token
        
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
                data = response.json()
                if response.status_code >= 400:
                    raise LocalRpcError(f"RPC error: {data.get('error', data)}")
            elif HAVE_REQUESTS:
                import requests
                response = requests.post(
                    self.rpc_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True
                )
                data = response.json()
                if response.status_code >= 400:
                    raise LocalRpcError(f"RPC error: {data.get('error', data)}")
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
        result = self._call("rpc.ping", [])
        return bool(result)
    
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
        head = {}
        try:
            head = self.get_chain_head()
        except Exception as exc:
            logger.debug(f"get_chain_head failed during sync status: {exc}")

        head_height = head.get("number", 0) or head.get("height", 0)
        head_height = head_height if head_height is not None else 0

        try:
            result = self._call("chain.getSyncStatus", [])
            if result:
                best_height = result.get("highestBlock", 0) or result.get("best_height", 0)
                best_height = best_height if best_height is not None else head_height
                return SyncStatus(
                    syncing=result.get("syncing", False),
                    current_height=head_height or result.get("currentBlock", 0) or result.get("current_height", 0),
                    best_height=best_height,
                    phase=result.get("phase", "idle"),
                    in_flight=result.get("in_flight", 0),
                    queued=result.get("queued", 0),
                    peer_count=result.get("peer_count", 0),
                    peers_in=result.get("peers_in", 0),
                    peers_out=result.get("peers_out", 0),
                    last_progress=result.get("last_progress"),
                    last_error=result.get("last_error"),
                )
        except Exception as exc:
            logger.debug(f"getSyncStatus failed: {exc}")

        return SyncStatus(
            syncing=False,
            current_height=head_height,
            best_height=head_height,
            phase="synced" if head_height else "idle",
        )
    
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


def _extract_methods(result: Any) -> set[str]:
    if not result:
        return set()
    if isinstance(result, list):
        return {item for item in result if isinstance(item, str)}
    if isinstance(result, dict):
        methods: set[str] = set()
        if isinstance(result.get("methods"), list):
            methods.update(item for item in result["methods"] if isinstance(item, str))
        methods.update(item for item in result.keys() if isinstance(item, str))
        for value in result.values():
            if isinstance(value, list):
                methods.update(item for item in value if isinstance(item, str))
        return methods
    return set()


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
