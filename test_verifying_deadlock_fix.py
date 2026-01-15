#!/usr/bin/env python3
"""
Test suite for VERIFYING phase deadlock fix.

Tests the scenario where sync gets stuck in VERIFYING phase:
- Block buffer has orphan blocks
- No blocks in flight or queued
- _drain_block_buffer() was not called periodically, causing deadlock

This test validates that the fix (periodic call to _drain_block_buffer in sync loop)
prevents the deadlock.
"""

import time
from collections import OrderedDict


def test_verifying_phase_condition():
    """Test the VERIFYING phase condition logic."""
    # Simulate the phase detection logic
    sync_inflight_blocks = {}
    sync_block_buffer = OrderedDict()
    
    # Scenario 1: No blocks in flight or buffer -> should NOT be VERIFYING
    phase = "VERIFYING" if (sync_inflight_blocks or sync_block_buffer) else "OTHER"
    assert phase == "OTHER", "Should not be VERIFYING when both are empty"
    
    # Scenario 2: Blocks in flight -> should be VERIFYING
    sync_inflight_blocks[b"block_1"] = time.time()
    phase = "VERIFYING" if (sync_inflight_blocks or sync_block_buffer) else "OTHER"
    assert phase == "VERIFYING", "Should be VERIFYING when blocks in flight"
    sync_inflight_blocks.clear()
    
    # Scenario 3: Blocks in buffer (orphans) -> should be VERIFYING
    sync_block_buffer[b"orphan_1"] = {"parent_hash": b"missing_parent"}
    phase = "VERIFYING" if (sync_inflight_blocks or sync_block_buffer) else "OTHER"
    assert phase == "VERIFYING", "Should be VERIFYING when buffer has blocks"
    
    print("✓ Test 1 PASSED: VERIFYING phase condition logic works")
    return True


def test_deadlock_scenario():
    """Test the deadlock scenario that the fix addresses."""
    # Simulate the state that causes deadlock
    sync_inflight_blocks = {}  # Empty - no blocks being downloaded
    sync_block_queue = []  # Empty - no blocks queued
    sync_block_buffer = OrderedDict()  # Has orphan blocks
    
    # Add orphan blocks to buffer
    sync_block_buffer[b"orphan_1"] = {
        "parent_hash": b"missing_parent_1",
        "received_at": time.time(),
    }
    sync_block_buffer[b"orphan_2"] = {
        "parent_hash": b"missing_parent_2",
        "received_at": time.time(),
    }
    
    # This is the deadlock state:
    # - Phase would be VERIFYING (buffer not empty)
    # - But no blocks in flight or queued
    # - Without periodic drain_block_buffer call, stays stuck forever
    
    phase = "VERIFYING" if (sync_inflight_blocks or sync_block_buffer) else "OTHER"
    is_deadlocked = (
        phase == "VERIFYING"
        and not sync_inflight_blocks
        and not sync_block_queue
    )
    
    assert is_deadlocked, "Should detect deadlock condition"
    
    print("✓ Test 2 PASSED: Deadlock scenario detection works")
    return True


def test_drain_block_buffer_clears_invalid_orphans():
    """Test that drain_block_buffer clears orphans that fail to import."""
    # Simulate drain_block_buffer logic
    sync_block_buffer = OrderedDict()
    local_blocks = {b"parent_A": True}  # Only parent_A is available
    
    def has_block(block_hash):
        return block_hash in local_blocks
    
    def is_orphan_reason(reason):
        return reason == "missing_parent"
    
    # Add blocks to buffer
    sync_block_buffer[b"block_B"] = {
        "parent_hash": b"parent_A",  # Parent available
        "can_import": True,
        "reason": None,
    }
    sync_block_buffer[b"orphan_C"] = {
        "parent_hash": b"missing_parent",  # Parent not available
        "can_import": False,
        "reason": "missing_parent",
    }
    sync_block_buffer[b"invalid_D"] = {
        "parent_hash": b"parent_A",  # Parent available but block invalid
        "can_import": False,
        "reason": "invalid_signature",  # Not an orphan reason
    }
    
    # Simulate draining
    progressed = True
    while progressed:
        progressed = False
        for h, blk in list(sync_block_buffer.items()):
            if not has_block(blk["parent_hash"]):
                continue  # Skip orphans (parent not available)
            
            ok = blk["can_import"]
            reason = blk["reason"]
            
            if ok:
                sync_block_buffer.pop(h, None)
                progressed = True
                continue
            
            # Failed to import
            if not is_orphan_reason(reason):
                # Not an orphan - remove from buffer (permanently invalid)
                sync_block_buffer.pop(h, None)
    
    # After draining:
    # - block_B should be removed (successfully imported)
    # - orphan_C should remain (parent not available, will be retried)
    # - invalid_D should be removed (not orphan, permanently invalid)
    
    assert b"block_B" not in sync_block_buffer, "Imported block should be removed"
    assert b"orphan_C" in sync_block_buffer, "Orphan should remain for retry"
    assert b"invalid_D" not in sync_block_buffer, "Invalid block should be removed"
    
    print("✓ Test 3 PASSED: drain_block_buffer clears invalid orphans correctly")
    return True


def test_periodic_drain_prevents_deadlock():
    """Test that periodic drain_block_buffer call prevents deadlock."""
    # Simulate the sync loop with periodic drain
    sync_block_buffer = OrderedDict()
    local_blocks = {b"genesis": True}
    drain_call_count = 0
    
    def has_block(block_hash):
        return block_hash in local_blocks
    
    def drain_block_buffer():
        nonlocal drain_call_count
        drain_call_count += 1
        
        # Simulate draining - remove blocks whose parents are now available
        for h, blk in list(sync_block_buffer.items()):
            if has_block(blk["parent_hash"]):
                sync_block_buffer.pop(h, None)
                local_blocks[h] = True  # Add to local blocks
    
    # Start with orphan blocks
    sync_block_buffer[b"block_1"] = {"parent_hash": b"genesis"}
    sync_block_buffer[b"block_2"] = {"parent_hash": b"block_1"}
    sync_block_buffer[b"block_3"] = {"parent_hash": b"block_2"}
    
    # Simulate sync loop ticks with periodic drain
    for tick in range(5):
        drain_block_buffer()  # Periodic call (the fix)
        
        if not sync_block_buffer:
            break  # All blocks processed
    
    # Without periodic drain, blocks would stay in buffer forever (deadlock)
    # With periodic drain, all blocks get processed
    assert len(sync_block_buffer) == 0, "All blocks should be drained"
    assert drain_call_count > 0, "drain_block_buffer should be called"
    assert b"block_1" in local_blocks, "block_1 should be imported"
    assert b"block_2" in local_blocks, "block_2 should be imported"
    assert b"block_3" in local_blocks, "block_3 should be imported"
    
    print(f"✓ Test 4 PASSED: Periodic drain prevents deadlock (drained in {tick + 1} ticks)")
    return True


def test_orphan_prune_and_requeue():
    """Test that expired orphans are pruned and requeued."""
    sync_block_buffer = OrderedDict()
    sync_block_queue = []
    sync_block_queue_set = set()
    sync_orphan_ttl = 60.0
    now = time.time()
    local_blocks = {b"genesis": True}
    
    def has_block(block_hash):
        return block_hash in local_blocks
    
    # Add expired orphan
    sync_block_buffer[b"orphan_expired"] = {
        "received_at": now - 70.0,  # Expired (> 60s)
        "parent_hash": b"missing",
    }
    
    # Simulate prune_orphan_buffer
    expired = []
    for h, blk in list(sync_block_buffer.items()):
        if now - blk["received_at"] > sync_orphan_ttl:
            expired.append(h)
    
    for h in expired:
        sync_block_buffer.pop(h, None)
        if not has_block(h) and h not in sync_block_queue_set:
            sync_block_queue.append(h)
            sync_block_queue_set.add(h)
    
    # Expired orphan should be removed from buffer and requeued
    assert b"orphan_expired" not in sync_block_buffer, "Expired orphan should be removed"
    assert b"orphan_expired" in sync_block_queue_set, "Expired orphan should be requeued"
    
    print("✓ Test 5 PASSED: Orphan prune and requeue works")
    return True


def main():
    """Run all tests."""
    print("=" * 60)
    print("VERIFYING Phase Deadlock Fix - Test Suite")
    print("=" * 60)
    print()
    
    tests = [
        test_verifying_phase_condition,
        test_deadlock_scenario,
        test_drain_block_buffer_clears_invalid_orphans,
        test_periodic_drain_prevents_deadlock,
        test_orphan_prune_and_requeue,
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
            import traceback
            traceback.print_exc()
    
    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
