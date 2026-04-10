"""
Caching utilities for hot paths.

This module provides caching for expensive operations:
- Transaction hash computation
- Signature message hash computation
- Block template caching with TTL

Caches use LRU eviction and are bounded to prevent memory growth.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Optional, Tuple


class LRUCache:
    """
    Simple LRU cache with size limit.
    
    Thread-safe for single-threaded async code. For multi-threaded use,
    add external locking.
    """
    
    def __init__(self, max_size: int = 1000):
        """
        Initialize LRU cache.
        
        Args:
            max_size: Maximum number of entries
        """
        self.max_size = max_size
        self.cache: OrderedDict = OrderedDict()
    
    def get(self, key: bytes) -> Optional[bytes]:
        """
        Get value from cache.
        
        Args:
            key: Cache key (bytes)
        
        Returns:
            Cached value or None if not found
        """
        if key not in self.cache:
            return None
        
        # Move to end (most recently used)
        self.cache.move_to_end(key)
        return self.cache[key]
    
    def put(self, key: bytes, value: bytes) -> None:
        """
        Put value in cache.
        
        Args:
            key: Cache key (bytes)
            value: Value to cache (bytes)
        """
        if key in self.cache:
            # Update value and move to end
            self.cache[key] = value
            self.cache.move_to_end(key)
        else:
            # Add new entry
            self.cache[key] = value
            
            # Evict oldest if over limit
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)
    
    def clear(self) -> None:
        """Clear all cache entries."""
        self.cache.clear()
    
    def size(self) -> int:
        """Get current cache size."""
        return len(self.cache)


class TxHashCache:
    """
    Cache for transaction hash computation.
    
    Caches the SHA3-256 hash of transaction CBOR bytes to avoid
    repeated hashing in hot paths (mempool admission, block validation).
    """
    
    def __init__(self, max_size: int = 10000):
        """
        Initialize transaction hash cache.
        
        Args:
            max_size: Maximum number of cached hashes
        """
        self.cache = LRUCache(max_size)
    
    def get_or_compute(self, tx_bytes: bytes) -> bytes:
        """
        Get cached hash or compute and cache it.
        
        Args:
            tx_bytes: Canonical CBOR transaction bytes
        
        Returns:
            SHA3-256 hash (32 bytes)
        """
        cached = self.cache.get(tx_bytes)
        if cached is not None:
            return cached
        
        # Compute hash
        hash_value = hashlib.sha3_256(tx_bytes).digest()
        
        # Cache it
        self.cache.put(tx_bytes, hash_value)
        
        return hash_value
    
    def invalidate(self) -> None:
        """Clear all cached hashes."""
        self.cache.clear()


class SignMsgCache:
    """
    Cache for signature message hashes.
    
    Caches the signing message for transactions to avoid repeated
    CBOR encoding/decoding in verification paths.
    """
    
    def __init__(self, max_size: int = 10000):
        """
        Initialize signature message cache.
        
        Args:
            max_size: Maximum number of cached messages
        """
        self.cache = LRUCache(max_size)
    
    def get_or_compute(self, tx_bytes: bytes, compute_fn) -> bytes:
        """
        Get cached signing message or compute it.
        
        Args:
            tx_bytes: Transaction identifier (usually CBOR bytes)
            compute_fn: Function to compute signing message if not cached
        
        Returns:
            Signing message bytes
        """
        cached = self.cache.get(tx_bytes)
        if cached is not None:
            return cached
        
        # Compute message
        msg = compute_fn()
        
        # Cache it
        self.cache.put(tx_bytes, msg)
        
        return msg
    
    def invalidate(self) -> None:
        """Clear all cached messages."""
        self.cache.clear()


class BlockTemplateCache:
    """
    Cache for block templates with TTL.
    
    Caches block templates to avoid rebuilding on every request.
    Invalidated when mempool changes or TTL expires.
    """
    
    def __init__(self, ttl_ms: int = 250):
        """
        Initialize block template cache.
        
        Args:
            ttl_ms: Time-to-live in milliseconds (default: 250ms)
        """
        self.ttl_ms = ttl_ms
        self.cached_template: Optional[Tuple[bytes, float]] = None
    
    def get(self) -> Optional[bytes]:
        """
        Get cached template if still valid.
        
        Returns:
            Cached template bytes or None if expired/invalid
        """
        if self.cached_template is None:
            return None
        
        template, timestamp = self.cached_template
        
        # Check TTL
        age_ms = (time.time() - timestamp) * 1000
        if age_ms > self.ttl_ms:
            self.cached_template = None
            return None
        
        return template
    
    def put(self, template: bytes) -> None:
        """
        Cache a new template.
        
        Args:
            template: Template bytes to cache
        """
        self.cached_template = (template, time.time())
    
    def invalidate(self) -> None:
        """Invalidate cached template."""
        self.cached_template = None


# Global cache instances (lazy initialization)
_tx_hash_cache: Optional[TxHashCache] = None
_sign_msg_cache: Optional[SignMsgCache] = None
_block_template_cache: Optional[BlockTemplateCache] = None


def get_tx_hash_cache() -> TxHashCache:
    """Get global transaction hash cache."""
    global _tx_hash_cache
    if _tx_hash_cache is None:
        _tx_hash_cache = TxHashCache()
    return _tx_hash_cache


def get_sign_msg_cache() -> SignMsgCache:
    """Get global signature message cache."""
    global _sign_msg_cache
    if _sign_msg_cache is None:
        _sign_msg_cache = SignMsgCache()
    return _sign_msg_cache


def get_block_template_cache(ttl_ms: int = 250) -> BlockTemplateCache:
    """
    Get global block template cache.
    
    Args:
        ttl_ms: TTL in milliseconds (default: 250ms)
    """
    global _block_template_cache
    if _block_template_cache is None:
        _block_template_cache = BlockTemplateCache(ttl_ms)
    return _block_template_cache


__all__ = [
    "LRUCache",
    "TxHashCache",
    "SignMsgCache",
    "BlockTemplateCache",
    "get_tx_hash_cache",
    "get_sign_msg_cache",
    "get_block_template_cache",
]
