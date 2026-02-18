"""
Rate limiter using token bucket algorithm.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Dict

logger = logging.getLogger(__name__)


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""
    capacity: int
    refill_rate: float  # tokens per second
    tokens: float
    last_refill: float


class RateLimiter:
    """Rate limiter with per-address and per-IP limits."""
    
    def __init__(
        self,
        requests_per_hour_address: int = 100,
        requests_per_hour_ip: int = 200,
    ):
        self.address_limit = requests_per_hour_address
        self.ip_limit = requests_per_hour_ip
        
        # Calculate refill rates (tokens per second)
        self.address_refill_rate = requests_per_hour_address / 3600.0
        self.ip_refill_rate = requests_per_hour_ip / 3600.0
        
        # Token buckets
        self.address_buckets: Dict[str, TokenBucket] = {}
        self.ip_buckets: Dict[str, TokenBucket] = {}
        
        # Locks for thread safety
        self.address_lock = Lock()
        self.ip_lock = Lock()
    
    def _create_bucket(
        self,
        capacity: int,
        refill_rate: float,
    ) -> TokenBucket:
        """Create a new token bucket."""
        return TokenBucket(
            capacity=capacity,
            refill_rate=refill_rate,
            tokens=float(capacity),
            last_refill=time.time(),
        )
    
    def _refill_bucket(self, bucket: TokenBucket):
        """Refill a token bucket based on elapsed time."""
        now = time.time()
        elapsed = now - bucket.last_refill
        
        # Calculate tokens to add
        tokens_to_add = elapsed * bucket.refill_rate
        bucket.tokens = min(bucket.capacity, bucket.tokens + tokens_to_add)
        bucket.last_refill = now
    
    def _try_consume(self, bucket: TokenBucket, tokens: int = 1) -> bool:
        """Try to consume tokens from a bucket."""
        self._refill_bucket(bucket)
        
        if bucket.tokens >= tokens:
            bucket.tokens -= tokens
            return True
        return False
    
    def check_address(self, address: str) -> bool:
        """
        Check if address is within rate limit.
        
        Args:
            address: Address to check
        
        Returns:
            True if allowed, False if rate limited
        """
        with self.address_lock:
            if address not in self.address_buckets:
                self.address_buckets[address] = self._create_bucket(
                    self.address_limit,
                    self.address_refill_rate,
                )
            
            bucket = self.address_buckets[address]
            allowed = self._try_consume(bucket)
            
            if not allowed:
                logger.warning(
                    f"Rate limit exceeded for address: {address}",
                    extra={"address": address, "tokens": bucket.tokens}
                )
            
            return allowed
    
    def check_ip(self, ip: str) -> bool:
        """
        Check if IP is within rate limit.
        
        Args:
            ip: IP address to check
        
        Returns:
            True if allowed, False if rate limited
        """
        with self.ip_lock:
            if ip not in self.ip_buckets:
                self.ip_buckets[ip] = self._create_bucket(
                    self.ip_limit,
                    self.ip_refill_rate,
                )
            
            bucket = self.ip_buckets[ip]
            allowed = self._try_consume(bucket)
            
            if not allowed:
                logger.warning(
                    f"Rate limit exceeded for IP: {ip}",
                    extra={"ip": ip, "tokens": bucket.tokens}
                )
            
            return allowed
    
    def check(self, address: str, ip: str) -> bool:
        """
        Check both address and IP rate limits.
        
        Args:
            address: Address to check
            ip: IP address to check
        
        Returns:
            True if both allowed, False if either is rate limited
        """
        # Check both limits
        address_ok = self.check_address(address)
        ip_ok = self.check_ip(ip)
        
        return address_ok and ip_ok
    
    def get_remaining(self, address: str) -> int:
        """Get remaining tokens for an address."""
        with self.address_lock:
            if address not in self.address_buckets:
                return self.address_limit
            
            bucket = self.address_buckets[address]
            self._refill_bucket(bucket)
            return int(bucket.tokens)
    
    def cleanup_old_buckets(self, max_age: int = 7200):
        """
        Clean up old buckets that haven't been used recently.
        
        Args:
            max_age: Maximum age in seconds (default: 2 hours)
        """
        now = time.time()
        
        with self.address_lock:
            to_remove = [
                addr for addr, bucket in self.address_buckets.items()
                if now - bucket.last_refill > max_age
            ]
            for addr in to_remove:
                del self.address_buckets[addr]
            
            if to_remove:
                logger.debug(f"Cleaned up {len(to_remove)} old address buckets")
        
        with self.ip_lock:
            to_remove = [
                ip for ip, bucket in self.ip_buckets.items()
                if now - bucket.last_refill > max_age
            ]
            for ip in to_remove:
                del self.ip_buckets[ip]
            
            if to_remove:
                logger.debug(f"Cleaned up {len(to_remove)} old IP buckets")
