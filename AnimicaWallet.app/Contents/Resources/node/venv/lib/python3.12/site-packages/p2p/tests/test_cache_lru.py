"""
Test the BlockHeaderExistenceCache LRU cache for sync performance.

This tests the in-memory LRU cache that dramatically reduces database
lookups when checking the same blocks/headers repeatedly during sync.
"""
import pytest
from p2p.sync.cache_store import BlockHeaderExistenceCache


def test_cache_basic_operations():
    """Test basic cache operations"""
    cache = BlockHeaderExistenceCache(max_size=100)
    
    # Initially empty
    assert cache.get_block(b'test1') is None
    assert cache.get_header(b'test2') is None
    
    # Add some entries
    cache.put_block(b'test1', True)
    cache.put_block(b'test2', False)
    cache.put_header(b'test3', True)
    cache.put_header(b'test4', False)
    
    # Retrieve entries
    assert cache.get_block(b'test1') is True
    assert cache.get_block(b'test2') is False
    assert cache.get_header(b'test3') is True
    assert cache.get_header(b'test4') is False
    
    # Unknown entry
    assert cache.get_block(b'unknown') is None


def test_cache_lru_eviction():
    """Test that LRU eviction works correctly"""
    cache = BlockHeaderExistenceCache(max_size=3)
    
    # Fill cache to capacity
    cache.put_block(b'block1', True)
    cache.put_block(b'block2', True)
    cache.put_block(b'block3', True)
    
    # All should be present
    assert cache.get_block(b'block1') is True
    assert cache.get_block(b'block2') is True
    assert cache.get_block(b'block3') is True
    
    # Add a 4th item, should evict oldest (block1)
    cache.put_block(b'block4', True)
    
    # block1 should be evicted
    assert cache.get_block(b'block1') is None
    
    # Others should still be present
    assert cache.get_block(b'block2') is True
    assert cache.get_block(b'block3') is True
    assert cache.get_block(b'block4') is True


def test_cache_lru_access_updates_order():
    """Test that accessing an item updates its position in LRU order"""
    cache = BlockHeaderExistenceCache(max_size=3)
    
    # Add three items
    cache.put_block(b'block1', True)
    cache.put_block(b'block2', True)
    cache.put_block(b'block3', True)
    
    # Access block1 to make it most recently used
    cache.get_block(b'block1')
    
    # Add a 4th item, should evict block2 (oldest unused)
    cache.put_block(b'block4', True)
    
    # block1 should still be present (was accessed)
    assert cache.get_block(b'block1') is True
    
    # block2 should be evicted (oldest unused)
    assert cache.get_block(b'block2') is None
    
    # Others should be present
    assert cache.get_block(b'block3') is True
    assert cache.get_block(b'block4') is True


def test_cache_separate_block_header_spaces():
    """Test that block and header caches are separate"""
    cache = BlockHeaderExistenceCache(max_size=2)
    
    # Add to block cache
    cache.put_block(b'item1', True)
    cache.put_block(b'item2', True)
    
    # Add to header cache (same hashes)
    cache.put_header(b'item1', False)
    cache.put_header(b'item2', False)
    
    # Each cache should have its own values
    assert cache.get_block(b'item1') is True
    assert cache.get_block(b'item2') is True
    assert cache.get_header(b'item1') is False
    assert cache.get_header(b'item2') is False
    
    # Adding 3rd item to block cache shouldn't affect header cache
    cache.put_block(b'item3', True)
    
    # Block cache should evict item1
    assert cache.get_block(b'item1') is None
    assert cache.get_block(b'item2') is True
    assert cache.get_block(b'item3') is True
    
    # Header cache should be unchanged
    assert cache.get_header(b'item1') is False
    assert cache.get_header(b'item2') is False


def test_cache_invalidation():
    """Test cache invalidation"""
    cache = BlockHeaderExistenceCache(max_size=100)
    
    # Add entries
    cache.put_block(b'block1', True)
    cache.put_block(b'block2', True)
    cache.put_header(b'header1', True)
    cache.put_header(b'header2', True)
    
    # Verify present
    assert cache.get_block(b'block1') is True
    assert cache.get_header(b'header1') is True
    
    # Invalidate specific entries
    cache.invalidate_block(b'block1')
    cache.invalidate_header(b'header1')
    
    # Should be gone
    assert cache.get_block(b'block1') is None
    assert cache.get_header(b'header1') is None
    
    # Others should remain
    assert cache.get_block(b'block2') is True
    assert cache.get_header(b'header2') is True


def test_cache_clear():
    """Test clearing the entire cache"""
    cache = BlockHeaderExistenceCache(max_size=100)
    
    # Add entries
    cache.put_block(b'block1', True)
    cache.put_block(b'block2', False)
    cache.put_header(b'header1', True)
    cache.put_header(b'header2', False)
    
    # Track some hits/misses
    cache.get_block(b'block1')
    cache.get_block(b'block1')
    cache.get_block(b'unknown')
    
    assert cache.hits > 0
    assert cache.misses > 0
    
    # Clear everything
    cache.clear()
    
    # All entries should be gone
    assert cache.get_block(b'block1') is None
    assert cache.get_block(b'block2') is None
    assert cache.get_header(b'header1') is None
    assert cache.get_header(b'header2') is None
    
    # Stats should be reset
    assert cache.hits == 0
    assert cache.misses == 0


def test_cache_stats():
    """Test cache statistics tracking"""
    cache = BlockHeaderExistenceCache(max_size=100)
    
    # Add some entries
    cache.put_block(b'block1', True)
    cache.put_block(b'block2', True)
    cache.put_header(b'header1', True)
    
    # Generate some hits
    cache.get_block(b'block1')  # hit
    cache.get_block(b'block1')  # hit
    cache.get_header(b'header1')  # hit
    
    # Generate some misses
    cache.get_block(b'unknown1')  # miss
    cache.get_block(b'unknown2')  # miss
    cache.get_header(b'unknown3')  # miss
    cache.get_header(b'unknown4')  # miss
    
    # Check stats
    stats = cache.stats()
    
    assert stats['hits'] == 3
    assert stats['misses'] == 4
    assert stats['hit_rate_pct'] == pytest.approx(42.86, abs=0.01)  # 3/7 = 42.86%
    assert stats['block_cache_size'] == 2
    assert stats['header_cache_size'] == 1
    assert stats['max_size'] == 100


def test_cache_large_scale():
    """Test cache behavior with large number of entries"""
    cache = BlockHeaderExistenceCache(max_size=1000)
    
    # Add 5000 blocks (should keep only last 1000)
    for i in range(5000):
        block_hash = i.to_bytes(32, 'big')
        cache.put_block(block_hash, True)
    
    # First 4000 should be evicted
    for i in range(4000):
        block_hash = i.to_bytes(32, 'big')
        assert cache.get_block(block_hash) is None
    
    # Last 1000 should remain
    for i in range(4000, 5000):
        block_hash = i.to_bytes(32, 'big')
        assert cache.get_block(block_hash) is True
    
    stats = cache.stats()
    assert stats['block_cache_size'] == 1000  # At capacity


def test_cache_hit_rate_improvement():
    """
    Demonstrate cache hit rate improvement in typical sync scenario.
    
    In sync, the same blocks are often checked multiple times:
    - First during header validation
    - Then during block download planning
    - Finally during actual download
    
    The cache dramatically reduces DB lookups in this scenario.
    """
    cache = BlockHeaderExistenceCache(max_size=5000)
    
    # Simulate checking 1000 blocks 3 times each (typical sync pattern)
    blocks = [i.to_bytes(32, 'big') for i in range(1000)]
    
    # First pass: all misses (populate cache)
    for block in blocks:
        result = cache.get_block(block)
        assert result is None  # Cache miss
        cache.put_block(block, True)
    
    # Second pass: all hits!
    hits_before = cache.hits
    for block in blocks:
        result = cache.get_block(block)
        assert result is True  # Cache hit
    
    hits_after = cache.hits
    assert hits_after - hits_before == 1000  # All hits
    
    # Third pass: all hits again!
    hits_before = cache.hits
    for block in blocks:
        result = cache.get_block(block)
        assert result is True  # Cache hit
    
    hits_after = cache.hits
    assert hits_after - hits_before == 1000  # All hits
    
    stats = cache.stats()
    # First pass: 1000 misses, Second + Third: 2000 hits
    # Total hit rate: 2000/(1000+2000) = 66.7%
    assert stats['hits'] == 2000
    assert stats['misses'] == 1000
    assert stats['hit_rate_pct'] == pytest.approx(66.67, abs=0.01)


def test_cache_memory_efficiency():
    """Test that cache uses reasonable memory"""
    # 10K entries at ~32 bytes per hash + overhead = ~320KB + overhead
    # Should be very memory efficient
    cache = BlockHeaderExistenceCache(max_size=10000)
    
    # Fill completely
    for i in range(10000):
        block_hash = i.to_bytes(32, 'big')
        header_hash = (i + 10000).to_bytes(32, 'big')
        cache.put_block(block_hash, True)
        cache.put_header(header_hash, True)
    
    stats = cache.stats()
    assert stats['block_cache_size'] == 10000
    assert stats['header_cache_size'] == 10000
    
    # Verify all entries still accessible
    assert cache.get_block((5000).to_bytes(32, 'big')) is True
    assert cache.get_header((15000).to_bytes(32, 'big')) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
