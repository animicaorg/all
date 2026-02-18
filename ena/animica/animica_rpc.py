"""
Robust JSON-RPC client for Animica with timeouts, retries, and circuit breaker.
"""

import logging
import time
from typing import Any, Dict, Optional
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Simple circuit breaker pattern implementation."""
    
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = CircuitState.CLOSED
    
    def call_succeeded(self):
        """Reset on successful call."""
        self.failure_count = 0
        self.state = CircuitState.CLOSED
    
    def call_failed(self):
        """Increment failure count and potentially open circuit."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                f"Circuit breaker opened after {self.failure_count} failures"
            )
    
    def can_attempt(self) -> bool:
        """Check if we can attempt a call."""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            # Check if timeout has passed
            if time.time() - self.last_failure_time >= self.timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker entering half-open state")
                return True
            return False
        
        # HALF_OPEN state - allow one attempt
        return True
    
    def is_open(self) -> bool:
        """Check if circuit is open."""
        return self.state == CircuitState.OPEN


class AnimicaRPCError(Exception):
    """Base exception for RPC errors."""
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"RPC Error {code}: {message}")


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


class AnimicaRPCClient:
    """Robust JSON-RPC client for Animica blockchain."""
    
    def __init__(
        self,
        rpc_url: str,
        timeout: int = 30,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_timeout: int = 60,
    ):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_threshold,
            timeout=circuit_breaker_timeout,
        )
        self.client = httpx.Client(timeout=timeout)
    
    def call(
        self,
        method: str,
        params: Optional[list] = None,
        retry: bool = True,
    ) -> Any:
        """
        Call a JSON-RPC method with retries and circuit breaker.
        
        Args:
            method: RPC method name
            params: Method parameters (list or None)
            retry: Whether to retry on failure
        
        Returns:
            RPC result
        
        Raises:
            AnimicaRPCError: On RPC error
            CircuitOpenError: If circuit breaker is open
            Exception: On network or other errors
        """
        if not self.circuit_breaker.can_attempt():
            raise CircuitOpenError("Circuit breaker is open - RPC unavailable")
        
        if params is None:
            params = []
        
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": method,
            "params": params,
        }
        
        last_error = None
        attempts = self.max_retries if retry else 1
        
        for attempt in range(attempts):
            try:
                logger.debug(
                    f"RPC call attempt {attempt + 1}/{attempts}: {method}",
                    extra={"method": method, "params": params}
                )
                
                response = self.client.post(
                    self.rpc_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                
                data = response.json()
                
                if "error" in data:
                    error = data["error"]
                    raise AnimicaRPCError(
                        code=error.get("code", -1),
                        message=error.get("message", "Unknown error"),
                        data=error.get("data"),
                    )
                
                # Success
                self.circuit_breaker.call_succeeded()
                return data.get("result")
            
            except (httpx.HTTPError, AnimicaRPCError) as e:
                last_error = e
                logger.warning(
                    f"RPC call failed (attempt {attempt + 1}/{attempts}): {e}"
                )
                
                # Don't retry on RPC errors (only network errors)
                if isinstance(e, AnimicaRPCError):
                    self.circuit_breaker.call_failed()
                    raise
                
                # Backoff before retry
                if attempt < attempts - 1:
                    backoff = self.retry_backoff ** attempt
                    logger.debug(f"Backing off for {backoff}s before retry")
                    time.sleep(backoff)
        
        # All retries exhausted
        self.circuit_breaker.call_failed()
        if last_error:
            raise last_error
        raise Exception("RPC call failed after all retries")
    
    def close(self):
        """Close the HTTP client."""
        self.client.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()
    
    # Convenience methods for common RPC calls
    
    def get_transaction(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        """Get transaction by hash."""
        methods_to_try = [
            "tx.getTransaction",
            "tx_getTransaction",
            "eth_getTransactionByHash",
        ]
        
        for method in methods_to_try:
            try:
                result = self.call(method, [tx_hash], retry=False)
                if result is not None:
                    return result
            except AnimicaRPCError as e:
                # Method not found - try next
                if e.code == -32601:
                    continue
                raise
        
        return None
    
    def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        """Get transaction receipt."""
        methods_to_try = [
            "tx.getTransactionReceipt",
            "tx_getTransactionReceipt",
            "eth_getTransactionReceipt",
        ]
        
        for method in methods_to_try:
            try:
                result = self.call(method, [tx_hash], retry=False)
                if result is not None:
                    return result
            except AnimicaRPCError as e:
                if e.code == -32601:
                    continue
                raise
        
        return None
    
    def get_balance(self, address: str) -> Optional[int]:
        """Get balance for address."""
        methods_to_try = [
            "state.getBalance",
            "state_getBalance",
            "chain_getBalance",
            "eth_getBalance",
        ]
        
        for method in methods_to_try:
            try:
                result = self.call(method, [address, "latest"], retry=True)
                if result is not None:
                    # Convert to int if hex string
                    if isinstance(result, str) and result.startswith("0x"):
                        return int(result, 16)
                    return int(result)
            except AnimicaRPCError as e:
                if e.code == -32601:
                    continue
                raise
        
        return None
    
    def send_raw_transaction(self, signed_tx: str) -> str:
        """Send a raw signed transaction."""
        methods_to_try = [
            "tx.sendRawTransaction",
            "tx_sendRawTransaction",
            "tx2.sendRawTransaction",
            "eth_sendRawTransaction",
        ]
        
        for method in methods_to_try:
            try:
                result = self.call(method, [signed_tx], retry=True)
                if result:
                    return result
            except AnimicaRPCError as e:
                if e.code == -32601:
                    continue
                raise
        
        raise Exception("Failed to send transaction - no method available")
    
    def get_pending_nonce(self, address: str) -> Optional[int]:
        """Get pending nonce for address."""
        methods_to_try = [
            "state.getPendingNonce",
            "state.getNextNonce",
            "state_getPendingNonce",
            "eth_getTransactionCount",
        ]
        
        for method in methods_to_try:
            try:
                result = self.call(method, [address, "pending"], retry=True)
                if result is not None:
                    if isinstance(result, str) and result.startswith("0x"):
                        return int(result, 16)
                    return int(result)
            except AnimicaRPCError as e:
                if e.code == -32601:
                    continue
                raise
        
        return None
