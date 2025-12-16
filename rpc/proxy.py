"""RPC proxy module for CLIENT-ONLY forwarding to external RPC endpoints.

DEPRECATED: This module is for client convenience only and must NOT be used
for node consensus, mining, or sync operations.

WARNING: Using this proxy for node operations would centralize trust and defeat
the purpose of P2P decentralization. Nodes should sync via P2P bootstrap seeds
(e.g., mainnet.animica.org for P2P, NOT rpc.animica.org for HTTP RPC).

This module forwards RPC requests to an external endpoint (disabled by default).
It is only suitable for:
  - Client-side wallet applications
  - Read-only queries from untrusted clients
  - Development/testing scenarios

It must NEVER be used for:
  - Node consensus or sync
  - Mining or block validation
  - Any operation requiring chain truth
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger("animica.rpc.proxy")


@dataclass
class ProxyConfig:
    """Configuration for RPC proxy (CLIENT-ONLY, disabled by default).
    
    SECURITY WARNING: Setting trusted_rpc_url enables centralized trust mode.
    This is ONLY safe for client read operations, NEVER for node consensus/mining.
    
    MIGRATION: If you previously relied on the default rpc.animica.org endpoint,
    you must now explicitly set ANIMICA_TRUSTED_RPC_URL environment variable.
    This is an intentional breaking change to prevent accidental centralization.
    """
    
    trusted_rpc_url: str | None = None  # MUST be explicitly set, no default
    max_retries: int = 3
    retry_delay_ms: int = 1000  # milliseconds between retries
    timeout_seconds: float = 30.0
    enable_caching: bool = False  # Future: implement response caching
    
    @classmethod
    def from_env(cls) -> ProxyConfig:
        """Load proxy config from environment variables.
        
        SECURITY: trusted_rpc_url is None by default. Explicitly set
        ANIMICA_TRUSTED_RPC_URL to enable proxy (client use only).
        """
        import os
        
        # NO DEFAULT - must be explicitly set via environment variable
        trusted_url = os.getenv("ANIMICA_TRUSTED_RPC_URL")
        max_retries = int(os.getenv("ANIMICA_PROXY_MAX_RETRIES", "3"))
        retry_delay = int(os.getenv("ANIMICA_PROXY_RETRY_DELAY_MS", "1000"))
        timeout = float(os.getenv("ANIMICA_PROXY_TIMEOUT_SECONDS", "30.0"))
        enable_cache = os.getenv("ANIMICA_PROXY_ENABLE_CACHE", "false").lower() == "true"
        
        return cls(
            trusted_rpc_url=trusted_url,
            max_retries=max_retries,
            retry_delay_ms=retry_delay,
            timeout_seconds=timeout,
            enable_caching=enable_cache,
        )


class RpcProxyError(Exception):
    """Base exception for RPC proxy errors."""
    pass


class ProxyConnectionError(RpcProxyError):
    """Raised when proxy cannot connect to trusted endpoint."""
    pass


class ProxyTimeoutError(RpcProxyError):
    """Raised when proxy request times out."""
    pass


class RpcProxy:
    """RPC proxy that forwards requests to trusted source of truth."""
    
    def __init__(self, config: Optional[ProxyConfig] = None):
        """Initialize RPC proxy (CLIENT-ONLY usage).
        
        Args:
            config: Optional proxy configuration. If None, loads from environment.
            
        Raises:
            ValueError: If trusted_rpc_url is not set (proxy disabled by default).
        """
        self.config = config or ProxyConfig.from_env()
        self._cache: dict[str, tuple[Any, float]] = {}  # method -> (result, timestamp)
        
        if not self.config.trusted_rpc_url:
            raise ValueError(
                "RPC Proxy is disabled by default. Set ANIMICA_TRUSTED_RPC_URL to enable.\n"
                "WARNING: Proxy is for CLIENT-ONLY use. Do NOT use for node consensus, mining, or sync.\n"
                "\n"
                "MIGRATION: If you previously relied on rpc.animica.org as the default endpoint,\n"
                "set ANIMICA_TRUSTED_RPC_URL=https://rpc.animica.org/rpc explicitly.\n"
                "This is an intentional breaking change to enforce P2P-first decentralization."
            )
        
        logger.warning(
            f"RPC Proxy enabled with trusted endpoint: {self.config.trusted_rpc_url}\n"
            "WARNING: This proxy is for CLIENT-ONLY operations. "
            "Do NOT use for node consensus, mining, or sync."
        )
    
    async def forward_request(
        self,
        method: str,
        params: Any,
        fallback_handler: Optional[Callable[[], Any]] = None,
    ) -> Any:
        """Forward RPC request to trusted endpoint with retry logic.
        
        Args:
            method: JSON-RPC method name
            params: Method parameters (list or dict)
            fallback_handler: Optional async callable to invoke if all retries fail
            
        Returns:
            Result from trusted endpoint or fallback handler
            
        Raises:
            ProxyConnectionError: If all retries fail and no fallback provided
            ProxyTimeoutError: If request times out
        """
        last_error = None
        
        for attempt in range(self.config.max_retries):
            try:
                result = await self._make_request(method, params)
                
                if attempt > 0:
                    logger.info(
                        f"Successfully forwarded {method} to trusted endpoint "
                        f"after {attempt + 1} attempts"
                    )
                
                return result
                
            except asyncio.TimeoutError as e:
                last_error = ProxyTimeoutError(
                    f"Request to {self.config.trusted_rpc_url} timed out"
                )
                logger.warning(
                    f"Attempt {attempt + 1}/{self.config.max_retries} timed out "
                    f"for {method}: {e}"
                )
                
            except Exception as e:
                last_error = ProxyConnectionError(
                    f"Failed to connect to {self.config.trusted_rpc_url}: {e}"
                )
                logger.warning(
                    f"Attempt {attempt + 1}/{self.config.max_retries} failed "
                    f"for {method}: {e}"
                )
            
            # Wait before retry (except on last attempt)
            if attempt < self.config.max_retries - 1:
                await asyncio.sleep(self.config.retry_delay_ms / 1000.0)
        
        # All retries exhausted
        logger.error(
            f"All {self.config.max_retries} attempts failed for {method}. "
            f"Last error: {last_error}"
        )
        
        # Try fallback handler if provided
        if fallback_handler is not None:
            logger.info(f"Invoking fallback handler for {method}")
            try:
                return await fallback_handler()
            except Exception as e:
                logger.error(f"Fallback handler also failed for {method}: {e}")
                raise ProxyConnectionError(
                    f"Both trusted endpoint and fallback failed: {last_error}, {e}"
                )
        
        # No fallback available
        raise last_error or ProxyConnectionError("Unknown proxy error")
    
    async def _make_request(self, method: str, params: Any) -> Any:
        """Make HTTP JSON-RPC request to trusted endpoint.
        
        Args:
            method: JSON-RPC method name
            params: Method parameters
            
        Returns:
            Result from JSON-RPC response
            
        Raises:
            Exception: On HTTP errors or invalid responses
        """
        try:
            import httpx
        except ImportError:
            raise ImportError(
                "httpx is required for RPC proxy. Install with: pip install httpx"
            )
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        
        logger.debug(
            f"Forwarding {method} to {self.config.trusted_rpc_url} "
            f"with params: {params}"
        )
        
        async with httpx.AsyncClient(
            timeout=self.config.timeout_seconds,
            follow_redirects=True  # Handle HTTP 307 redirects gracefully
        ) as client:
            response = await client.post(
                self.config.trusted_rpc_url,
                json=payload,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if "error" in data:
                error = data["error"]
                raise Exception(
                    f"RPC error from trusted endpoint: "
                    f"code={error.get('code')}, message={error.get('message')}"
                )
            
            if "result" not in data:
                raise Exception(
                    f"Invalid JSON-RPC response from trusted endpoint: {data}"
                )
            
            logger.debug(f"Successfully received response for {method}")
            return data["result"]
    
    def sync_forward_request(
        self,
        method: str,
        params: Any,
        fallback_handler: Optional[Callable[[], Any]] = None,
    ) -> Any:
        """Synchronous wrapper for forward_request.
        
        This is a convenience method for non-async contexts.
        
        Args:
            method: JSON-RPC method name
            params: Method parameters
            fallback_handler: Optional callable (sync or async) to invoke if all retries fail
            
        Returns:
            Result from trusted endpoint or fallback handler
        """
        # Wrap fallback handler if it's sync
        async_fallback = None
        if fallback_handler is not None:
            if asyncio.iscoroutinefunction(fallback_handler):
                async_fallback = fallback_handler
            else:
                async def _wrap():
                    return fallback_handler()
                async_fallback = _wrap
        
        # Run in event loop
        # Check if we're already in an async context
        try:
            asyncio.get_running_loop()
            # Already in event loop - cannot use sync_forward_request
            raise RuntimeError(
                "sync_forward_request called from async context - use forward_request instead"
            )
        except RuntimeError as e:
            # Check if this is "no running loop" vs "called from async context"
            if "sync_forward_request called from async context" in str(e):
                # Re-raise our intentional error
                raise
            # No running loop - this is the expected case, create new loop
            return asyncio.run(
                self.forward_request(method, params, async_fallback)
            )


def create_proxy(config: Optional[ProxyConfig] = None) -> RpcProxy:
    """Factory function to create RPC proxy instance.
    
    Args:
        config: Optional proxy configuration
        
    Returns:
        Configured RpcProxy instance
    """
    return RpcProxy(config)


__all__ = [
    "ProxyConfig",
    "RpcProxy",
    "RpcProxyError",
    "ProxyConnectionError",
    "ProxyTimeoutError",
    "create_proxy",
]
