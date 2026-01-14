#!/usr/bin/env python3
"""
Test to verify the sync expected_height bug fix.

This test simulates the scenario where:
1. Blocks 11044-11162 are queued
2. Some blocks (11044-11050) are already in flight or processed
3. The remaining blocks (11051-11162) should still be requested
4. WITHOUT the fix, they would all be deferred because expected_height stays at 11044
5. WITH the fix, expected_height increments when skipping blocks, so 11051+ can be requested
"""

import asyncio
from collections import deque
from typing import Dict, List, Optional, Set


class MockSyncState:
    """Simplified mock of the sync state for testing the fix."""
    
    def __init__(self):
        # Queue state
        self._sync_block_queue: deque = deque()
        self._sync_block_queue_set: Set[bytes] = set()
        self._sync_block_queue_heights: Dict[bytes, int] = {}
        
        # In-flight state
        self._sync_inflight_blocks: Dict[bytes, float] = {}
        
        # Existing blocks
        self._has_blocks: Set[bytes] = set()
        
        # Config
        self._sync_max_inflight = 2048
        
        # Local state
        self.local_height = 11043
        
    def queue_blocks(self, heights: List[int]):
        """Queue blocks by height."""
        for height in heights:
            block_hash = f"block_{height}".encode()
            self._sync_block_queue.append(block_hash)
            self._sync_block_queue_set.add(block_hash)
            self._sync_block_queue_heights[block_hash] = height
    
    def mark_blocks_inflight(self, heights: List[int]):
        """Mark blocks as in-flight."""
        for height in heights:
            block_hash = f"block_{height}".encode()
            self._sync_inflight_blocks[block_hash] = 0.0
    
    def mark_blocks_exist(self, heights: List[int]):
        """Mark blocks as already existing."""
        for height in heights:
            block_hash = f"block_{height}".encode()
            self._has_blocks.add(block_hash)
    
    def _has_block(self, h: bytes) -> bool:
        return h in self._has_blocks
    
    async def schedule_block_requests_old(self) -> List[int]:
        """
        OLD VERSION (with bug): doesn't update expected_height when skipping blocks.
        Returns list of heights that would be requested.
        """
        expected_height = self.local_height + 1  # 11044
        
        queued = list(self._sync_block_queue)
        self._sync_block_queue.clear()
        
        # Sort by height
        ordered = sorted(
            queued,
            key=lambda h: self._sync_block_queue_heights.get(h, 1_000_000_000),
        )
        
        to_request: List[bytes] = []
        deferred: List[tuple[bytes, Optional[int]]] = []
        
        for h in ordered:
            height_hint = self._sync_block_queue_heights.get(h)
            
            # Skip blocks that already exist or are in flight
            if (
                self._has_block(h)
                or h in self._sync_inflight_blocks
            ):
                self._sync_block_queue_set.discard(h)
                self._sync_block_queue_heights.pop(h, None)
                # BUG: expected_height NOT updated here!
                continue
            
            # Defer blocks ahead of expected_height
            if height_hint is not None and height_hint > expected_height:
                deferred.append((h, height_hint))
                continue
            
            # Add to request list
            self._sync_block_queue_set.discard(h)
            self._sync_block_queue_heights.pop(h, None)
            to_request.append(h)
            
            # Update expected_height
            if height_hint == expected_height:
                expected_height += 1
        
        # Re-queue deferred blocks
        for h, height_hint in deferred:
            self._sync_block_queue.append(h)
            self._sync_block_queue_set.add(h)
            if height_hint is not None:
                self._sync_block_queue_heights[h] = height_hint
        
        # Return heights that would be requested
        return [self._sync_block_queue_heights.get(h, -1) for h in to_request if h in self._sync_block_queue_heights]
    
    async def schedule_block_requests_new(self) -> List[int]:
        """
        NEW VERSION (with fix): updates expected_height when skipping blocks.
        Returns list of heights that would be requested.
        """
        expected_height = self.local_height + 1  # 11044
        
        queued = list(self._sync_block_queue)
        self._sync_block_queue.clear()
        
        # Sort by height
        ordered = sorted(
            queued,
            key=lambda h: self._sync_block_queue_heights.get(h, 1_000_000_000),
        )
        
        to_request: List[bytes] = []
        to_request_heights: List[int] = []  # Track heights before we remove them
        deferred: List[tuple[bytes, Optional[int]]] = []
        
        for h in ordered:
            height_hint = self._sync_block_queue_heights.get(h)
            
            # Skip blocks that already exist or are in flight
            if (
                self._has_block(h)
                or h in self._sync_inflight_blocks
            ):
                self._sync_block_queue_set.discard(h)
                self._sync_block_queue_heights.pop(h, None)
                # FIX: Update expected_height when skipping blocks at expected height
                if height_hint is not None and height_hint == expected_height:
                    expected_height += 1
                continue
            
            # Defer blocks ahead of expected_height
            if height_hint is not None and height_hint > expected_height:
                deferred.append((h, height_hint))
                continue
            
            # Add to request list
            self._sync_block_queue_set.discard(h)
            to_request.append(h)
            if height_hint is not None:
                to_request_heights.append(height_hint)
            self._sync_block_queue_heights.pop(h, None)
            
            # Update expected_height
            if height_hint == expected_height:
                expected_height += 1
        
        # Re-queue deferred blocks
        for h, height_hint in deferred:
            self._sync_block_queue.append(h)
            self._sync_block_queue_set.add(h)
            if height_hint is not None:
                self._sync_block_queue_heights[h] = height_hint
        
        return to_request_heights


async def test_sync_with_skipped_blocks_old():
    """Test OLD version shows the bug."""
    print("\n=== Testing OLD version (with bug) ===")
    state = MockSyncState()
    
    # Queue blocks 11044-11162
    state.queue_blocks(list(range(11044, 11163)))
    print(f"Queued blocks: 11044-11162 ({len(state._sync_block_queue)} blocks)")
    
    # Mark blocks 11044-11050 as in-flight (already requested)
    state.mark_blocks_inflight(list(range(11044, 11051)))
    print(f"Marked blocks 11044-11050 as in-flight")
    
    # Try to schedule more block requests
    requested = await state.schedule_block_requests_old()
    print(f"Blocks requested: {requested}")
    print(f"Deferred back to queue: {len(state._sync_block_queue)} blocks")
    
    # Bug: expected_height stayed at 11044, so blocks 11051+ were all deferred
    if len(requested) == 0:
        print("✗ BUG CONFIRMED: No blocks requested (all deferred)")
        return False
    else:
        print(f"✓ No bug: {len(requested)} blocks requested")
        return True


async def test_sync_with_skipped_blocks_new():
    """Test NEW version with the fix."""
    print("\n=== Testing NEW version (with fix) ===")
    state = MockSyncState()
    
    # Queue blocks 11044-11162
    state.queue_blocks(list(range(11044, 11163)))
    print(f"Queued blocks: 11044-11162 ({len(state._sync_block_queue)} blocks)")
    
    # Mark blocks 11044-11050 as in-flight (already requested)
    state.mark_blocks_inflight(list(range(11044, 11051)))
    print(f"Marked blocks 11044-11050 as in-flight")
    
    # Try to schedule more block requests
    requested = await state.schedule_block_requests_new()
    print(f"Blocks requested: {sorted(requested)[:10]}... (showing first 10)")
    print(f"Total blocks requested: {len(requested)}")
    print(f"Deferred back to queue: {len(state._sync_block_queue)} blocks")
    
    # Fix: expected_height increments when skipping inflight blocks, so 11051+ can be requested
    if len(requested) > 0 and min(requested) == 11051:
        print("✓ FIX WORKS: Blocks 11051+ were requested")
        return True
    else:
        print(f"✗ FIX FAILED: Expected blocks starting at 11051, got {requested[:5] if requested else 'none'}")
        return False


async def main():
    print("=" * 70)
    print("Sync Expected Height Bug Fix Test")
    print("=" * 70)
    
    # Test old version (should show bug)
    old_result = await test_sync_with_skipped_blocks_old()
    
    # Test new version (should be fixed)
    new_result = await test_sync_with_skipped_blocks_new()
    
    print("\n" + "=" * 70)
    print("Summary:")
    print(f"  Old version (buggy): {'PASS (unexpectedly)' if old_result else 'FAIL (expected - shows bug)'}")
    print(f"  New version (fixed): {'PASS (expected)' if new_result else 'FAIL (unexpected)'}")
    print("=" * 70)
    
    if not old_result and new_result:
        print("\n✓ TEST PASSED: Fix successfully resolves the issue!")
        return 0
    else:
        print("\n✗ TEST FAILED: Fix did not resolve the issue")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
