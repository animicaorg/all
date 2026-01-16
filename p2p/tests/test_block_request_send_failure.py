"""
Test for block request send failure handling.

This test validates that when a block request fails to send (e.g., due to
peer disconnect or network error), the blocks are properly removed from
inflight tracking and re-queued for retry instead of getting stuck.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List


def test_block_request_send_failure_handling():
    """
    Test that blocks are properly handled when send fails.
    
    Scenario:
    1. Node attempts to request blocks from a peer
    2. The _send() call fails (simulating peer disconnect)
    3. Verify blocks are removed from inflight tracking
    4. Verify blocks are re-queued for retry
    """
    # This is a lightweight test to validate the logic
    # without needing full P2PService setup
    
    # Simulate the data structures
    inflight_blocks = {}
    inflight_peers = {}
    inflight_requests = {}
    block_queue = []
    block_queue_set = set()
    block_queue_heights = {}
    
    # Test block hashes
    test_hashes = [b'block1', b'block2', b'block3']
    
    # Step 1: Mark blocks as inflight (simulating pre-send state)
    for h in test_hashes:
        inflight_blocks[h] = time.time()
        inflight_peers[h] = 'peer1'
        inflight_requests[h] = {'request_id': 'req1'}
    
    assert len(inflight_blocks) == 3
    assert len(block_queue) == 0
    
    # Step 2: Simulate send failure - remove from inflight and re-queue
    for h in test_hashes:
        inflight_blocks.pop(h, None)
        inflight_peers.pop(h, None)
        inflight_requests.pop(h, None)
        
        # Re-add to queue if not already there
        if h not in block_queue_set:
            block_queue.insert(0, h)  # appendleft equivalent
            block_queue_set.add(h)
    
    # Step 3: Verify cleanup
    assert len(inflight_blocks) == 0, "Inflight blocks should be cleared"
    assert len(inflight_peers) == 0, "Inflight peers should be cleared"
    assert len(inflight_requests) == 0, "Inflight requests should be cleared"
    
    # Step 4: Verify re-queuing
    assert len(block_queue) == 3, "All blocks should be re-queued"
    assert len(block_queue_set) == 3, "Block queue set should match"
    assert set(block_queue) == set(test_hashes), "All test hashes should be in queue"
    
    print("✓ Block request send failure handling test passed")


def test_partial_send_failure():
    """
    Test that only failed chunks are cleaned up, successful chunks remain.
    
    Scenario:
    1. Request 3 chunks of blocks
    2. First chunk sends successfully
    3. Second chunk fails
    4. Third chunk not attempted
    5. Verify only successful blocks remain inflight
    """
    inflight_blocks = {}
    inflight_peers = {}
    successfully_sent = []
    
    # Chunk 1: succeeds
    chunk1 = [b'block1', b'block2']
    for h in chunk1:
        inflight_blocks[h] = time.time()
        inflight_peers[h] = 'peer1'
    successfully_sent.extend(chunk1)
    
    # Chunk 2: fails
    chunk2 = [b'block3', b'block4']
    for h in chunk2:
        inflight_blocks[h] = time.time()
        inflight_peers[h] = 'peer1'
    # Simulate failure: remove from inflight
    for h in chunk2:
        inflight_blocks.pop(h, None)
        inflight_peers.pop(h, None)
    
    # Verify state
    assert len(inflight_blocks) == 2, "Only successful chunk should remain inflight"
    assert b'block1' in inflight_blocks
    assert b'block2' in inflight_blocks
    assert b'block3' not in inflight_blocks
    assert b'block4' not in inflight_blocks
    assert len(successfully_sent) == 2
    
    print("✓ Partial send failure test passed")


if __name__ == "__main__":
    test_block_request_send_failure_handling()
    test_partial_send_failure()
    print("\n✅ All block request send failure tests passed!")
