"""
Integration test for P2P2 sync with out-of-order blocks.

This test validates that the orphan pool correctly handles blocks
arriving out of order and successfully syncs from genesis.
"""

import pytest
import asyncio
from typing import Dict, List, Optional


# Mock chain store for testing
class MockChainStore:
    """Mock chain storage for testing."""
    
    def __init__(self):
        self.blocks: Dict[str, Dict] = {}
        self.headers: Dict[str, Dict] = {}
        self.blocks_by_height: Dict[int, Dict] = {}
        self.head_height = 0
        self.head_hash = "genesis"
        
        # Add genesis
        genesis = {
            "hash": "genesis",
            "parent_hash": "0x0",
            "height": 0,
        }
        self.blocks["genesis"] = genesis
        self.headers["genesis"] = genesis
        self.blocks_by_height[0] = genesis
    
    async def get_head_height(self) -> int:
        return self.head_height
    
    async def get_head_hash(self) -> str:
        return self.head_hash
    
    async def get_block_hash(self, height: int) -> Optional[str]:
        block = self.blocks_by_height.get(height)
        return block["hash"] if block else None
    
    async def has_header(self, block_hash: str) -> bool:
        return block_hash in self.headers
    
    async def has_block(self, block_hash: str) -> bool:
        return block_hash in self.blocks
    
    async def get_header(self, block_hash: str) -> Optional[Dict]:
        return self.headers.get(block_hash)
    
    async def get_block(self, block_hash: str) -> Optional[Dict]:
        return self.blocks.get(block_hash)
    
    async def get_block_at_height(self, height: int) -> Optional[Dict]:
        return self.blocks_by_height.get(height)
    
    async def store_headers(self, headers: List[Dict]) -> bool:
        for header in headers:
            self.headers[header["hash"]] = header
        return True
    
    async def store_block(self, block: Dict) -> bool:
        """Store block and update head."""
        block_hash = block["hash"]
        height = block["height"]
        
        self.blocks[block_hash] = block
        self.blocks_by_height[height] = block
        
        # Update head if this is the next height
        if height == self.head_height + 1:
            self.head_height = height
            self.head_hash = block_hash
        
        return True


# Mock peer manager
class MockPeerManager:
    """Mock peer manager for testing."""
    
    def __init__(self):
        self.peers = {}
        self.connections = {}
    
    def get_connected_peers(self):
        return []


@pytest.mark.asyncio
async def test_out_of_order_sync():
    """
    Test syncing blocks that arrive out of order.
    
    Scenario:
    - Blocks arrive: 5, 3, 1, 4, 2
    - Orphan pool should hold them until parents arrive
    - Final chain should be: genesis -> 1 -> 2 -> 3 -> 4 -> 5
    """
    from p2p2.sync.blocks import BlocksSync, BlocksSyncConfig
    
    # Setup
    chain_store = MockChainStore()
    peer_manager = MockPeerManager()
    
    config = BlocksSyncConfig(
        window_size=100,
        orphan_ttl=300.0,
    )
    
    blocks_sync = BlocksSync(
        config=config,
        chain_store=chain_store,
        peer_manager=peer_manager,
    )
    
    await blocks_sync.start()
    
    # Create chain of blocks
    blocks = []
    for i in range(1, 6):
        parent_hash = "genesis" if i == 1 else f"block{i-1}"
        block = {
            "hash": f"block{i}",
            "parent_hash": parent_hash,
            "height": i,
        }
        blocks.append(block)
    
    # Deliver out of order: 5, 3, 1, 4, 2
    order = [4, 2, 0, 3, 1]  # Indices into blocks array
    
    for idx in order:
        block = blocks[idx]
        await blocks_sync.handle_block("test_peer", block)
        
        # Small delay
        await asyncio.sleep(0.01)
    
    # Wait a bit for cascade
    await asyncio.sleep(0.1)
    
    # Verify final state
    final_height = await chain_store.get_head_height()
    assert final_height == 5, f"Expected height 5, got {final_height}"
    
    # Verify all blocks stored
    for i in range(1, 6):
        assert await chain_store.has_block(f"block{i}"), f"Block {i} not stored"
    
    # Verify orphan pool is empty (all resolved)
    assert blocks_sync.orphan_pool.size() == 0, "Orphan pool should be empty"
    
    # Verify stats
    assert blocks_sync.stats.blocks_received == 5
    assert blocks_sync.stats.blocks_stored == 5
    assert blocks_sync.stats.orphans_resolved >= 3  # At least 3 orphans resolved (block1 wasn't orphaned since genesis exists)
    
    await blocks_sync.stop()
    
    print("✓ Out-of-order sync test PASSED")


@pytest.mark.asyncio
async def test_missing_parent_recovery():
    """
    Test recovery from missing parent scenario.
    
    Scenario:
    - Receive block 10 (parent=9 missing)
    - Should add to orphan pool
    - Should request parent
    - When parent arrives, should cascade
    """
    from p2p2.sync.blocks import BlocksSync, BlocksSyncConfig
    
    # Setup
    chain_store = MockChainStore()
    peer_manager = MockPeerManager()
    
    config = BlocksSyncConfig()
    blocks_sync = BlocksSync(
        config=config,
        chain_store=chain_store,
        peer_manager=peer_manager,
    )
    
    await blocks_sync.start()
    
    # Receive orphan block
    orphan = {
        "hash": "block10",
        "parent_hash": "block9",
        "height": 10,
    }
    
    await blocks_sync.handle_block("test_peer", orphan)
    await asyncio.sleep(0.01)
    
    # Verify it's in orphan pool
    assert blocks_sync.orphan_pool.size() == 1
    assert "block9" in blocks_sync.orphan_pool.get_missing_parents()
    
    # Stats should show orphan received
    assert blocks_sync.stats.orphans_received == 1
    assert blocks_sync.stats.blocks_stored == 0  # Not stored yet
    
    # Now deliver parent
    parent = {
        "hash": "block9",
        "parent_hash": "genesis",  # Simplified - connect to genesis
        "height": 9,
    }
    
    await blocks_sync.handle_block("test_peer", parent)
    await asyncio.sleep(0.1)
    
    # Both blocks should now be stored
    assert await chain_store.has_block("block9")
    assert await chain_store.has_block("block10")
    
    # Orphan pool should be empty
    assert blocks_sync.orphan_pool.size() == 0
    
    # Stats should show orphan resolved
    assert blocks_sync.stats.orphans_resolved == 1
    assert blocks_sync.stats.blocks_stored == 2
    
    await blocks_sync.stop()
    
    print("✓ Missing parent recovery test PASSED")


@pytest.mark.asyncio
async def test_long_chain_gap():
    """
    Test handling a long gap in the chain.
    
    Scenario:
    - Have blocks 0-5
    - Receive block 100 (huge gap)
    - Should orphan and wait for intermediate blocks
    """
    from p2p2.sync.blocks import BlocksSync, BlocksSyncConfig
    
    chain_store = MockChainStore()
    peer_manager = MockPeerManager()
    
    # Store blocks 1-5 first
    for i in range(1, 6):
        parent_hash = "genesis" if i == 1 else f"block{i-1}"
        block = {
            "hash": f"block{i}",
            "parent_hash": parent_hash,
            "height": i,
        }
        await chain_store.store_block(block)
    
    config = BlocksSyncConfig()
    blocks_sync = BlocksSync(
        config=config,
        chain_store=chain_store,
        peer_manager=peer_manager,
    )
    
    await blocks_sync.start()
    
    # Receive block far in future
    future_block = {
        "hash": "block100",
        "parent_hash": "block99",
        "height": 100,
    }
    
    await blocks_sync.handle_block("test_peer", future_block)
    await asyncio.sleep(0.01)
    
    # Should be orphaned
    assert blocks_sync.orphan_pool.size() == 1
    assert "block99" in blocks_sync.orphan_pool.get_missing_parents()
    
    # Not stored yet
    assert not await chain_store.has_block("block100")
    
    await blocks_sync.stop()
    
    print("✓ Long chain gap test PASSED")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
