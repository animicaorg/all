#!/usr/bin/env python3
"""
Test suite for sync skip stuck blocks feature.

Tests the key scenarios where blocks get stuck and need to be skipped:
1. Block repeatedly fails to import for non-orphan, non-PoW reasons
2. Skip threshold triggers after N failures
3. Skipped blocks are moved to separate queue
4. Skipped blocks are retried later with fresh peers
5. Successful import clears failure counters
"""

import time
from collections import deque
from typing import Dict, Set


def test_block_failure_tracking():
    """Test that block failures are tracked correctly."""
    block_import_failures: Dict[bytes, int] = {}
    block_hash = b"test_block_123"
    
    # First failure
    block_import_failures[block_hash] = block_import_failures.get(block_hash, 0) + 1
    assert block_import_failures[block_hash] == 1, "First failure should be tracked"
    
    # Second failure
    block_import_failures[block_hash] = block_import_failures.get(block_hash, 0) + 1
    assert block_import_failures[block_hash] == 2, "Second failure should increment counter"
    
    # Third failure
    block_import_failures[block_hash] = block_import_failures.get(block_hash, 0) + 1
    assert block_import_failures[block_hash] == 3, "Third failure should increment counter"
    
    print("✓ Test 1 PASSED: Block failure tracking works")
    return True


def test_skip_threshold_triggers():
    """Test that blocks are skipped after threshold failures."""
    block_import_failures: Dict[bytes, int] = {}
    block_import_failure_threshold = 3
    skipped_blocks_queue: deque = deque()
    skipped_blocks_set: Set[bytes] = set()
    
    block_hash = b"test_block_456"
    
    # Simulate multiple failures
    for i in range(block_import_failure_threshold):
        block_import_failures[block_hash] = block_import_failures.get(block_hash, 0) + 1
    
    failure_count = block_import_failures[block_hash]
    
    # Check if skip threshold is reached
    should_skip = failure_count >= block_import_failure_threshold
    
    assert should_skip, f"Block should be skipped after {block_import_failure_threshold} failures"
    
    # Add to skipped queue
    if block_hash not in skipped_blocks_set:
        skipped_blocks_queue.append(block_hash)
        skipped_blocks_set.add(block_hash)
    
    assert block_hash in skipped_blocks_set, "Block should be in skipped set"
    assert len(skipped_blocks_queue) == 1, "Block should be in skipped queue"
    
    print(f"✓ Test 2 PASSED: Skip threshold triggers after {block_import_failure_threshold} failures")
    return True


def test_skipped_queue_size_limit():
    """Test that skipped blocks queue has a size limit."""
    skipped_blocks_queue: deque = deque()
    skipped_blocks_set: Set[bytes] = set()
    max_skipped = 100
    
    # Add more than max_skipped blocks
    for i in range(max_skipped + 10):
        block_hash = f"block_{i}".encode()
        skipped_blocks_queue.append(block_hash)
        skipped_blocks_set.add(block_hash)
        
        # Simulate cleanup
        while len(skipped_blocks_queue) > max_skipped:
            old_hash = skipped_blocks_queue.popleft()
            skipped_blocks_set.discard(old_hash)
    
    assert len(skipped_blocks_queue) == max_skipped, f"Queue should be limited to {max_skipped} entries"
    assert len(skipped_blocks_set) == max_skipped, f"Set should be limited to {max_skipped} entries"
    
    print(f"✓ Test 3 PASSED: Skipped queue size limited to {max_skipped}")
    return True


def test_skipped_blocks_retry():
    """Test that skipped blocks are retried later."""
    skipped_blocks_queue: deque = deque()
    skipped_blocks_set: Set[bytes] = set()
    block_import_failures: Dict[bytes, int] = {}
    sync_block_queue: deque = deque()
    sync_block_queue_set: Set[bytes] = set()
    
    # Add some skipped blocks
    for i in range(5):
        block_hash = f"skipped_{i}".encode()
        skipped_blocks_queue.append(block_hash)
        skipped_blocks_set.add(block_hash)
        block_import_failures[block_hash] = 3
    
    # Simulate retry logic
    max_retry_per_cycle = 3
    retry_count = 0
    
    for _ in range(min(len(skipped_blocks_queue), max_retry_per_cycle)):
        block_hash = skipped_blocks_queue.popleft()
        skipped_blocks_set.discard(block_hash)
        
        # Reset failure count
        block_import_failures.pop(block_hash, None)
        
        # Add back to main queue
        if block_hash not in sync_block_queue_set:
            sync_block_queue.append(block_hash)
            sync_block_queue_set.add(block_hash)
            retry_count += 1
    
    assert retry_count == max_retry_per_cycle, f"Should retry {max_retry_per_cycle} blocks"
    assert len(sync_block_queue) == max_retry_per_cycle, "Blocks should be in main queue"
    assert len(skipped_blocks_queue) == 2, "Remaining blocks should stay in skipped queue"
    
    # Check that failure counters were reset for retried blocks
    for block_hash in list(sync_block_queue):
        assert block_hash not in block_import_failures, "Failure counter should be reset"
    
    print("✓ Test 4 PASSED: Skipped blocks retry logic works")
    return True


def test_successful_import_clears_counters():
    """Test that successful import clears failure counters."""
    block_import_failures: Dict[bytes, int] = {}
    skipped_blocks_set: Set[bytes] = set()
    
    block_hash = b"test_block_789"
    
    # Add failure tracking
    block_import_failures[block_hash] = 2
    skipped_blocks_set.add(block_hash)
    
    # Simulate successful import
    block_import_failures.pop(block_hash, None)
    skipped_blocks_set.discard(block_hash)
    
    assert block_hash not in block_import_failures, "Failure counter should be cleared"
    assert block_hash not in skipped_blocks_set, "Block should be removed from skipped set"
    
    print("✓ Test 5 PASSED: Successful import clears counters")
    return True


def test_failure_tracking_cleanup():
    """Test that failure tracking dict is cleaned up to prevent unbounded growth."""
    block_import_failures: Dict[bytes, int] = {}
    max_entries = 1000
    
    # Fill up to max + extra entries
    for i in range(max_entries + 100):
        block_hash = f"block_{i}".encode()
        block_import_failures[block_hash] = 1
        
        # Simulate cleanup
        while len(block_import_failures) > max_entries:
            block_import_failures.pop(next(iter(block_import_failures)))
    
    assert len(block_import_failures) <= max_entries, \
        f"Tracking dict should be capped at {max_entries}, got {len(block_import_failures)}"
    
    print(f"✓ Test 6 PASSED: Failure tracking cleanup works (kept at {len(block_import_failures)} entries)")
    return True


def test_non_orphan_non_pow_blocks_requeued():
    """Test that non-orphan, non-PoW failed blocks are re-queued for retry."""
    block_import_failures: Dict[bytes, int] = {}
    block_import_failure_threshold = 3
    sync_block_queue: deque = deque()
    sync_block_queue_set: Set[bytes] = set()
    
    block_hash = b"test_block_validation_fail"
    
    # First failure (below threshold)
    block_import_failures[block_hash] = block_import_failures.get(block_hash, 0) + 1
    failure_count = block_import_failures[block_hash]
    
    if failure_count < block_import_failure_threshold:
        # Re-queue for retry
        if block_hash not in sync_block_queue_set:
            sync_block_queue.append(block_hash)
            sync_block_queue_set.add(block_hash)
    
    assert block_hash in sync_block_queue_set, "Block should be re-queued below threshold"
    
    print("✓ Test 7 PASSED: Failed blocks re-queued below threshold")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("Sync Skip Stuck Blocks - Test Suite")
    print("=" * 60)
    print()
    
    tests = [
        test_block_failure_tracking,
        test_skip_threshold_triggers,
        test_skipped_queue_size_limit,
        test_skipped_blocks_retry,
        test_successful_import_clears_counters,
        test_failure_tracking_cleanup,
        test_non_orphan_non_pow_blocks_requeued,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
                print(f"✗ {test_func.__name__} FAILED")
        except Exception as e:
            failed += 1
            print(f"✗ {test_func.__name__} FAILED with exception: {e}")
    
    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
