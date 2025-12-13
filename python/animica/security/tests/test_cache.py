"""
Tests for caching utilities.
"""

import hashlib
import time

import pytest

from animica.security.cache import (
    BlockTemplateCache,
    LRUCache,
    SignMsgCache,
    TxHashCache,
)


class TestLRUCache:
    """Tests for LRU cache."""

    def test_basic_get_put(self):
        cache = LRUCache(max_size=3)
        
        cache.put(b"key1", b"value1")
        assert cache.get(b"key1") == b"value1"
        
        cache.put(b"key2", b"value2")
        assert cache.get(b"key2") == b"value2"
        
        assert cache.get(b"nonexistent") is None

    def test_lru_eviction(self):
        cache = LRUCache(max_size=3)
        
        # Fill cache
        cache.put(b"key1", b"value1")
        cache.put(b"key2", b"value2")
        cache.put(b"key3", b"value3")
        
        # All should be present
        assert cache.size() == 3
        assert cache.get(b"key1") == b"value1"
        
        # Add fourth item (should evict oldest: key2)
        cache.put(b"key4", b"value4")
        
        assert cache.size() == 3
        assert cache.get(b"key1") == b"value1"  # Still present (was accessed)
        assert cache.get(b"key2") is None  # Evicted (oldest unused)
        assert cache.get(b"key3") == b"value3"
        assert cache.get(b"key4") == b"value4"

    def test_update_moves_to_end(self):
        cache = LRUCache(max_size=3)
        
        cache.put(b"key1", b"value1")
        cache.put(b"key2", b"value2")
        cache.put(b"key3", b"value3")
        
        # Access key1 (moves to end)
        cache.get(b"key1")
        
        # Add key4 (should evict key2, not key1)
        cache.put(b"key4", b"value4")
        
        assert cache.get(b"key1") == b"value1"
        assert cache.get(b"key2") is None  # Evicted
        assert cache.get(b"key3") == b"value3"

    def test_clear(self):
        cache = LRUCache(max_size=3)
        
        cache.put(b"key1", b"value1")
        cache.put(b"key2", b"value2")
        
        assert cache.size() == 2
        
        cache.clear()
        
        assert cache.size() == 0
        assert cache.get(b"key1") is None


class TestTxHashCache:
    """Tests for transaction hash cache."""

    def test_compute_hash(self):
        cache = TxHashCache(max_size=10)
        
        tx_bytes = b"test transaction"
        expected_hash = hashlib.sha3_256(tx_bytes).digest()
        
        # First call computes
        hash1 = cache.get_or_compute(tx_bytes)
        assert hash1 == expected_hash
        
        # Second call uses cache
        hash2 = cache.get_or_compute(tx_bytes)
        assert hash2 == expected_hash
        assert hash1 == hash2

    def test_cache_different_txs(self):
        cache = TxHashCache(max_size=10)
        
        tx1 = b"transaction 1"
        tx2 = b"transaction 2"
        
        hash1 = cache.get_or_compute(tx1)
        hash2 = cache.get_or_compute(tx2)
        
        assert hash1 != hash2
        assert hash1 == hashlib.sha3_256(tx1).digest()
        assert hash2 == hashlib.sha3_256(tx2).digest()

    def test_invalidate(self):
        cache = TxHashCache(max_size=10)
        
        tx_bytes = b"test transaction"
        hash1 = cache.get_or_compute(tx_bytes)
        
        cache.invalidate()
        
        # Should recompute after invalidation
        hash2 = cache.get_or_compute(tx_bytes)
        assert hash2 == hash1  # Same value
        # But was recomputed (can't test directly without instrumentation)


class TestSignMsgCache:
    """Tests for signature message cache."""

    def test_compute_message(self):
        cache = SignMsgCache(max_size=10)
        
        tx_bytes = b"transaction"
        expected_msg = b"signing message"
        
        def compute_fn():
            return expected_msg
        
        # First call computes
        msg1 = cache.get_or_compute(tx_bytes, compute_fn)
        assert msg1 == expected_msg
        
        # Second call uses cache
        msg2 = cache.get_or_compute(tx_bytes, compute_fn)
        assert msg2 == expected_msg

    def test_different_txs(self):
        cache = SignMsgCache(max_size=10)
        
        tx1 = b"tx1"
        tx2 = b"tx2"
        
        msg1 = cache.get_or_compute(tx1, lambda: b"msg1")
        msg2 = cache.get_or_compute(tx2, lambda: b"msg2")
        
        assert msg1 == b"msg1"
        assert msg2 == b"msg2"

    def test_invalidate(self):
        cache = SignMsgCache(max_size=10)
        
        tx_bytes = b"tx"
        compute_count = [0]
        
        def compute_fn():
            compute_count[0] += 1
            return b"message"
        
        # First call
        cache.get_or_compute(tx_bytes, compute_fn)
        assert compute_count[0] == 1
        
        # Second call (cached)
        cache.get_or_compute(tx_bytes, compute_fn)
        assert compute_count[0] == 1  # Not recomputed
        
        # Invalidate and call again
        cache.invalidate()
        cache.get_or_compute(tx_bytes, compute_fn)
        assert compute_count[0] == 2  # Recomputed


class TestBlockTemplateCache:
    """Tests for block template cache."""

    def test_basic_caching(self):
        cache = BlockTemplateCache(ttl_ms=1000)  # 1 second TTL
        
        template = b"block template"
        
        # Put template
        cache.put(template)
        
        # Get should return cached value
        cached = cache.get()
        assert cached == template

    def test_ttl_expiration(self):
        cache = BlockTemplateCache(ttl_ms=100)  # 100ms TTL
        
        template = b"block template"
        cache.put(template)
        
        # Should be cached immediately
        assert cache.get() == template
        
        # Wait for expiration
        time.sleep(0.15)  # 150ms
        
        # Should be expired
        assert cache.get() is None

    def test_invalidate(self):
        cache = BlockTemplateCache(ttl_ms=1000)
        
        template = b"block template"
        cache.put(template)
        
        assert cache.get() == template
        
        # Invalidate
        cache.invalidate()
        
        # Should be gone
        assert cache.get() is None

    def test_update_template(self):
        cache = BlockTemplateCache(ttl_ms=1000)
        
        template1 = b"template 1"
        template2 = b"template 2"
        
        cache.put(template1)
        assert cache.get() == template1
        
        # Update
        cache.put(template2)
        assert cache.get() == template2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
