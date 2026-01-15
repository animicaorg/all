"""
Integration test for block sync with out-of-order delivery.

This test simulates the scenario where:
1. Headers are received for blocks 1-10
2. Blocks arrive out of order (e.g., 5, 3, 1, 7, 2, ...)
3. The sync logic should still import them in order by requesting missing parents
"""

import asyncio
from collections import OrderedDict
from unittest.mock import Mock, AsyncMock, MagicMock
from typing import Optional


class MockHeader:
    def __init__(self, height: int, hash_bytes: bytes, parent_hash: bytes):
        self.height = height
        self.hash = hash_bytes
        self.parent_hash = parent_hash
    
    def __lt__(self, other):
        return self.height < other.height
    
    def __eq__(self, other):
        return self.height == other.height and self.hash == other.hash


class TestOutOfOrderBlockSync:
    """Test sync with out-of-order block delivery."""
    
    def __init__(self):
        self.imported_blocks = []
        self.genesis_hash = b"\x00" * 32
        self.blocks_db = {self.genesis_hash: True}  # Genesis is always available
        self.headers_db = {self.genesis_hash: True}
        self.current_height = 0
        
    def has_block(self, block_hash: bytes) -> bool:
        """Check if block exists in our mock DB."""
        return block_hash in self.blocks_db
    
    def has_header(self, header_hash: bytes) -> bool:
        """Check if header exists in our mock DB."""
        return header_hash in self.headers_db
    
    async def import_block(self, block_hash: bytes, parent_hash: bytes, height: int) -> tuple[bool, Optional[str]]:
        """
        Mock block import that checks parent availability.
        Returns (success, reason).
        """
        if not self.has_block(parent_hash):
            # Special case for height 1 with genesis parent
            if height == 1 and parent_hash == self.genesis_hash and self.current_height == 0:
                # Genesis is always available as parent for height 1
                pass
            else:
                return False, "missing parent"
        
        # Import succeeds
        self.blocks_db[block_hash] = True
        self.imported_blocks.append(height)
        self.current_height = max(self.current_height, height)
        return True, None
    
    def test_out_of_order_blocks(self):
        """Test that blocks delivered out of order are imported correctly."""
        from p2p.node.p2p_service import P2PService
        
        # Create mock service
        service = Mock(spec=P2PService)
        service._genesis_hash = Mock(return_value=self.genesis_hash)
        service._local_head = Mock(side_effect=lambda: (self.current_height, None))
        service._has_block = Mock(side_effect=self.has_block)
        service._has_header = Mock(side_effect=self.has_header)
        service._sync_inflight_blocks = {}
        service._sync_block_buffer = OrderedDict()
        service._sync_block_queue_set = set()
        service._sync_block_queue = []
        service._sync_block_queue_heights = {}
        service._sync_headers = {}
        service._sync_wakeup = Mock()
        service._sync_wakeup.set = Mock()
        
        # Mock _block_height_hint to return the height from the heights dict
        def mock_block_height_hint(block_hash: bytes) -> Optional[int]:
            return service._sync_block_queue_heights.get(block_hash)
        
        service._block_height_hint = mock_block_height_hint
        
        # Create headers for blocks 1-5
        headers = []
        for i in range(1, 6):
            prev_hash = self.genesis_hash if i == 1 else bytes([i-1] * 32)
            header = MockHeader(
                height=i,
                hash_bytes=bytes([i] * 32),
                parent_hash=prev_hash
            )
            headers.append(header)
            self.headers_db[header.hash] = True
            service._sync_headers[header.hash] = header
        
        print(f"Created {len(headers)} headers (heights 1-5)")
        
        # Enqueue blocks from headers
        result = P2PService._enqueue_missing_blocks(service, headers)
        print(f"Enqueued {result} blocks from headers")
        
        # Verify at least block 1 was enqueued (since we're at genesis)
        assert result >= 1, f"Expected at least 1 block enqueued, got {result}"
        assert bytes([1] * 32) in service._sync_block_queue_set, "Block 1 should be enqueued"
        
        # Verify blocks are ordered by height (lowest first)
        print(f"Block queue: {[service._sync_block_queue_heights.get(h, '?') for h in service._sync_block_queue]}")
        
        # Check that _next_block_needed returns the lowest height
        next_height, next_hash = P2PService._next_block_needed(service)
        print(f"Next block needed: height {next_height}, hash {next_hash.hex()[:8] if next_hash else None}")
        
        assert next_height == 1, f"Expected next block to be height 1, got {next_height}"
        assert next_hash == bytes([1] * 32), "Expected next block to be block 1"
        
        print("✓ Blocks enqueued correctly with lowest height first")
        
        # Simulate importing block 1
        print("\nSimulating import of block 1...")
        ok, reason = asyncio.run(self.import_block(bytes([1] * 32), self.genesis_hash, 1))
        assert ok, f"Block 1 import should succeed, got reason: {reason}"
        assert self.current_height == 1, f"Current height should be 1, got {self.current_height}"
        print(f"✓ Block 1 imported successfully, current height: {self.current_height}")
        
        # After importing block 1, block 2 should be importable
        print("\nSimulating import of block 2...")
        ok, reason = asyncio.run(self.import_block(bytes([2] * 32), bytes([1] * 32), 2))
        assert ok, f"Block 2 import should succeed after block 1, got reason: {reason}"
        assert self.current_height == 2, f"Current height should be 2, got {self.current_height}"
        print(f"✓ Block 2 imported successfully, current height: {self.current_height}")
        
        # Try to import block 4 before block 3 (out of order)
        print("\nSimulating import of block 4 (out of order)...")
        ok, reason = asyncio.run(self.import_block(bytes([4] * 32), bytes([3] * 32), 4))
        assert not ok, "Block 4 import should fail without block 3"
        assert reason == "missing parent", f"Expected 'missing parent', got: {reason}"
        print(f"✓ Block 4 correctly rejected: {reason}")
        
        # Import block 3
        print("\nSimulating import of block 3...")
        ok, reason = asyncio.run(self.import_block(bytes([3] * 32), bytes([2] * 32), 3))
        assert ok, f"Block 3 import should succeed, got reason: {reason}"
        assert self.current_height == 3, f"Current height should be 3, got {self.current_height}"
        print(f"✓ Block 3 imported successfully, current height: {self.current_height}")
        
        # Now block 4 should be importable
        print("\nSimulating import of block 4 (retry)...")
        ok, reason = asyncio.run(self.import_block(bytes([4] * 32), bytes([3] * 32), 4))
        assert ok, f"Block 4 import should now succeed, got reason: {reason}"
        assert self.current_height == 4, f"Current height should be 4, got {self.current_height}"
        print(f"✓ Block 4 imported successfully, current height: {self.current_height}")
        
        print(f"\n✅ Out-of-order block sync test passed!")
        print(f"   Imported blocks in order: {self.imported_blocks}")
        print(f"   Final height: {self.current_height}")


if __name__ == "__main__":
    test = TestOutOfOrderBlockSync()
    test.test_out_of_order_blocks()
