"""
Unit tests for orphan pool and parent backfill.
"""

import pytest
import time
from p2p2.sync.blocks import OrphanPool


def test_orphan_pool_basic():
    """Test basic orphan pool operations."""
    pool = OrphanPool(max_size=10, ttl=60.0)
    
    # Add orphan
    block = {
        "hash": "block1",
        "parent_hash": "parent1",
        "height": 100,
    }
    
    pool.add(block)
    assert pool.size() == 1
    assert "parent1" in pool.get_missing_parents()


def test_orphan_pool_get_children():
    """Test getting orphans by parent."""
    pool = OrphanPool()
    
    # Add orphans with same parent
    block1 = {"hash": "child1", "parent_hash": "parent_abc"}
    block2 = {"hash": "child2", "parent_hash": "parent_abc"}
    block3 = {"hash": "child3", "parent_hash": "parent_xyz"}
    
    pool.add(block1)
    pool.add(block2)
    pool.add(block3)
    
    # Get children of parent_abc
    children = pool.get_children("parent_abc")
    assert len(children) == 2
    hashes = [c["hash"] for c in children]
    assert "child1" in hashes
    assert "child2" in hashes


def test_orphan_pool_removal():
    """Test removing orphans."""
    pool = OrphanPool()
    
    block = {"hash": "block1", "parent_hash": "parent1"}
    pool.add(block)
    assert pool.size() == 1
    
    pool.remove("block1")
    assert pool.size() == 0
    assert "parent1" not in pool.get_missing_parents()


def test_orphan_pool_size_limit():
    """Test orphan pool size limit."""
    pool = OrphanPool(max_size=3)
    
    # Add 5 blocks
    for i in range(5):
        block = {
            "hash": f"block{i}",
            "parent_hash": f"parent{i}",
        }
        pool.add(block)
    
    # Should only keep 3 (evict oldest)
    assert pool.size() <= 3


def test_orphan_pool_cleanup_old():
    """Test cleanup of expired orphans."""
    pool = OrphanPool(ttl=1.0)  # 1 second TTL
    
    # Add block
    block = {"hash": "block1", "parent_hash": "parent1"}
    pool.add(block)
    assert pool.size() == 1
    
    # Wait for expiry
    time.sleep(1.5)
    
    # Cleanup
    pool.cleanup_old()
    assert pool.size() == 0


def test_orphan_cascade_scenario():
    """
    Test cascade scenario:
    - Receive block3 (parent=block2) -> orphan
    - Receive block2 (parent=block1) -> orphan
    - Receive block1 -> should enable cascade
    """
    pool = OrphanPool()
    
    # Receive in reverse order
    block3 = {"hash": "block3", "parent_hash": "block2", "height": 103}
    block2 = {"hash": "block2", "parent_hash": "block1", "height": 102}
    
    pool.add(block3)
    pool.add(block2)
    
    assert pool.size() == 2
    assert "block1" in pool.get_missing_parents()
    assert "block2" in pool.get_missing_parents()
    
    # When block1 arrives, we can get children
    children_of_block1 = pool.get_children("block1")
    assert len(children_of_block1) == 1
    assert children_of_block1[0]["hash"] == "block2"
    
    # After processing block2, we can get children
    children_of_block2 = pool.get_children("block2")
    assert len(children_of_block2) == 1
    assert children_of_block2[0]["hash"] == "block3"


def test_missing_parents_dedup():
    """Test that missing parents are deduplicated."""
    pool = OrphanPool()
    
    # Multiple orphans with same parent
    for i in range(5):
        block = {
            "hash": f"block{i}",
            "parent_hash": "common_parent",
        }
        pool.add(block)
    
    missing = pool.get_missing_parents()
    assert len(missing) == 1
    assert "common_parent" in missing


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
