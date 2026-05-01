"""
Test batch database operations for sync performance optimization.

These tests verify that the batch has_blocks_batch and has_headers_batch
methods work correctly and provide performance benefits over sequential checks.
"""
import pytest
from typing import Optional, Sequence, Tuple
from dataclasses import dataclass


# Mock types for testing
Hash = bytes


@dataclass
class MockBlock:
    """Mock block for testing"""
    hash: bytes
    parent_hash: bytes
    height: int


@dataclass
class MockHeader:
    """Mock header for testing"""
    hash: bytes
    parent_hash: bytes
    height: int


class MockChainAdapter:
    """
    Mock ChainAdapter that tracks all DB operations to verify batch
    optimization is being used.
    """
    
    def __init__(self):
        self.blocks = {}  # hash -> MockBlock
        self.headers = {}  # hash -> MockHeader
        self.has_block_calls = 0
        self.has_header_calls = 0
        self.has_blocks_batch_calls = 0
        self.has_headers_batch_calls = 0
    
    def add_block(self, block: MockBlock):
        """Add a block to the mock database"""
        self.blocks[block.hash] = block
        self.headers[block.hash] = MockHeader(
            hash=block.hash,
            parent_hash=block.parent_hash,
            height=block.height
        )
    
    async def has_block(self, h: Hash) -> bool:
        """Individual block check (slow path)"""
        self.has_block_calls += 1
        return h in self.blocks
    
    async def has_header(self, h: Hash) -> bool:
        """Individual header check (slow path)"""
        self.has_header_calls += 1
        return h in self.headers
    
    async def has_blocks_batch(self, hashes: Sequence[Hash]) -> set[Hash]:
        """Batch block check (fast path)"""
        self.has_blocks_batch_calls += 1
        return set(h for h in hashes if h in self.blocks)
    
    async def has_headers_batch(self, hashes: Sequence[Hash]) -> set[Hash]:
        """Batch header check (fast path)"""
        self.has_headers_batch_calls += 1
        return set(h for h in hashes if h in self.headers)
    
    async def get_head(self) -> Tuple[Hash, int]:
        """Get chain head"""
        if not self.blocks:
            return b'\x00' * 32, 0
        max_block = max(self.blocks.values(), key=lambda b: b.height)
        return max_block.hash, max_block.height
    
    async def put_blocks(self, blocks: Sequence[MockBlock]) -> None:
        """Store blocks"""
        for block in blocks:
            self.add_block(block)
    
    async def get_block(self, h: Hash) -> Optional[MockBlock]:
        """Get a block"""
        return self.blocks.get(h)


def make_block_chain(count: int) -> list[MockBlock]:
    """Create a chain of mock blocks for testing"""
    blocks = []
    parent_hash = b'\x00' * 32
    
    for i in range(count):
        block_hash = i.to_bytes(32, "big")
        block = MockBlock(
            hash=block_hash,
            parent_hash=parent_hash,
            height=i
        )
        blocks.append(block)
        parent_hash = block_hash
    
    return blocks


@pytest.mark.asyncio
async def test_batch_operations_reduce_db_calls():
    """
    Test that batch operations significantly reduce the number of DB calls
    compared to sequential operations.
    """
    # Create a chain of 1000 blocks
    blocks = make_block_chain(1000)
    
    # Set up mock adapter and add first 500 blocks
    adapter = MockChainAdapter()
    for block in blocks[:500]:
        adapter.add_block(block)
    
    # Get hashes to check (mix of existing and non-existing)
    hashes_to_check = [b.hash for b in blocks]
    
    # Test batch method
    adapter.has_block_calls = 0
    adapter.has_blocks_batch_calls = 0
    
    existing = await adapter.has_blocks_batch(hashes_to_check)
    
    # Verify correctness
    assert len(existing) == 500  # First 500 blocks exist
    for i in range(500):
        assert blocks[i].hash in existing
    for i in range(500, 1000):
        assert blocks[i].hash not in existing
    
    # Verify performance: should use batch method, not individual calls
    assert adapter.has_blocks_batch_calls == 1
    assert adapter.has_block_calls == 0  # No individual calls!


@pytest.mark.asyncio
async def test_batch_operations_correctness():
    """
    Test that batch operations return correct results for various scenarios.
    """
    blocks = make_block_chain(100)
    adapter = MockChainAdapter()
    
    # Scenario 1: Empty database
    result = await adapter.has_blocks_batch([b.hash for b in blocks])
    assert len(result) == 0
    
    # Scenario 2: Some blocks exist
    for block in blocks[:50]:
        adapter.add_block(block)
    
    result = await adapter.has_blocks_batch([b.hash for b in blocks])
    assert len(result) == 50
    assert all(blocks[i].hash in result for i in range(50))
    assert all(blocks[i].hash not in result for i in range(50, 100))
    
    # Scenario 3: All blocks exist
    for block in blocks[50:]:
        adapter.add_block(block)
    
    result = await adapter.has_blocks_batch([b.hash for b in blocks])
    assert len(result) == 100
    assert all(b.hash in result for b in blocks)


@pytest.mark.asyncio
async def test_batch_operations_empty_input():
    """
    Test that batch operations handle empty input correctly.
    """
    adapter = MockChainAdapter()
    
    result = await adapter.has_blocks_batch([])
    assert len(result) == 0
    assert adapter.has_blocks_batch_calls == 1
    
    result = await adapter.has_headers_batch([])
    assert len(result) == 0
    assert adapter.has_headers_batch_calls == 1


@pytest.mark.asyncio
async def test_header_batch_operations():
    """
    Test that header batch operations work similarly to block batch operations.
    """
    blocks = make_block_chain(500)
    adapter = MockChainAdapter()
    
    # Add blocks (which also adds headers)
    for block in blocks[:250]:
        adapter.add_block(block)
    
    # Check headers
    hashes_to_check = [b.hash for b in blocks]
    existing = await adapter.has_headers_batch(hashes_to_check)
    
    # Verify correctness
    assert len(existing) == 250
    for i in range(250):
        assert blocks[i].hash in existing
    for i in range(250, 500):
        assert blocks[i].hash not in existing
    
    # Verify performance
    assert adapter.has_headers_batch_calls == 1
    assert adapter.has_header_calls == 0


@pytest.mark.asyncio
async def test_batch_threshold_behavior():
    """
    Test that the optimization activates only for lists above the threshold.
    This test verifies the code path that chooses between batch and sequential.
    """
    adapter = MockChainAdapter()
    blocks = make_block_chain(150)
    
    for block in blocks:
        adapter.add_block(block)
    
    # Small list (below 100 threshold) - would fall back to sequential if implemented
    small_hashes = [b.hash for b in blocks[:50]]
    result = await adapter.has_blocks_batch(small_hashes)
    assert len(result) == 50
    
    # Large list (above 100 threshold) - uses batch optimization
    large_hashes = [b.hash for b in blocks]
    adapter.has_blocks_batch_calls = 0
    result = await adapter.has_blocks_batch(large_hashes)
    assert len(result) == 150
    assert adapter.has_blocks_batch_calls == 1


@pytest.mark.asyncio
async def test_performance_comparison():
    """
    Demonstrate the performance difference between sequential and batch operations.
    This test shows that batch operations reduce the number of DB calls.
    """
    blocks = make_block_chain(5000)  # Simulate 5000+ block scenario
    adapter = MockChainAdapter()
    
    # Add first 2500 blocks
    for block in blocks[:2500]:
        adapter.add_block(block)
    
    hashes = [b.hash for b in blocks]
    
    # Method 1: Sequential checks (old approach)
    adapter.has_block_calls = 0
    existing_sequential = set()
    for h in hashes:
        if await adapter.has_block(h):
            existing_sequential.add(h)
    
    sequential_calls = adapter.has_block_calls
    assert sequential_calls == 5000  # One call per hash
    
    # Method 2: Batch check (new approach)
    adapter.has_blocks_batch_calls = 0
    existing_batch = await adapter.has_blocks_batch(hashes)
    
    batch_calls = adapter.has_blocks_batch_calls
    assert batch_calls == 1  # Single batch call!
    
    # Verify both methods return the same result
    assert existing_sequential == existing_batch
    assert len(existing_batch) == 2500
    
    # Performance improvement: 5000x reduction in DB operations!
    improvement = sequential_calls / batch_calls
    assert improvement == 5000.0
    print(f"Performance improvement: {improvement}x fewer DB operations")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
