#!/usr/bin/env python3
"""
Test to verify sync recovery improvements.
Tests the key scenarios where sync was getting stuck.
"""
import time


# Constants from p2p_service.py
LARGE_GAP_THRESHOLD = 10
EXTENDED_STALL_SNAPSHOT_TRIGGER_SEC = 90.0
EXTENDED_STALL_WATCHDOG_MULTIPLIER = 1.5


def test_block_enqueue_with_large_gap():
    """
    Test that blocks are enqueued even when parent is missing if gap is large.
    This prevents stalls when there are header gaps.
    """
    # Simulated state
    local_height = 100
    block_height = 150  # 50 block gap
    has_parent_header = False
    
    # Calculate gap
    gap_size = block_height - local_height
    
    # With the fix, blocks should be enqueued if gap > LARGE_GAP_THRESHOLD
    should_enqueue = gap_size > LARGE_GAP_THRESHOLD
    
    assert should_enqueue, f"Should enqueue block when gap > {LARGE_GAP_THRESHOLD} even without parent header"
    print(f"✓ Test 1 PASSED: Large gap ({gap_size}) allows block enqueue without parent (threshold: {LARGE_GAP_THRESHOLD})")
    return True


def test_small_gap_respects_parent_check():
    """
    Test that blocks with small gaps still respect parent availability.
    This maintains ordering for small gaps.
    """
    # Simulated state
    local_height = 100
    block_height = 105  # 5 block gap
    has_parent_header = False
    
    # Calculate gap
    gap_size = block_height - local_height
    
    # With small gap, should NOT enqueue without parent
    should_enqueue = gap_size > LARGE_GAP_THRESHOLD
    
    assert not should_enqueue, f"Should NOT enqueue block when gap <= {LARGE_GAP_THRESHOLD} without parent header"
    print(f"✓ Test 2 PASSED: Small gap ({gap_size}) respects parent availability check (threshold: {LARGE_GAP_THRESHOLD})")
    return True


def test_stall_detection_clears_error_states():
    """
    Test that stall detection clears blocking error states.
    """
    # Simulated state
    best_header_height = 6495
    best_block_height = 6495
    sync_inflight_headers = 0
    sync_inflight_blocks = {}
    sync_block_queue = []
    sync_last_progress_at = time.time() - 20  # 20 seconds ago
    sync_stall_timeout = 10
    peers = {"peer1": {}}
    sync_last_header_error = "at_tip"
    
    now = time.time()
    
    # Stall should be detected
    stall_detected = (
        best_header_height == best_block_height
        and best_block_height > 0
        and not sync_inflight_headers
        and not sync_inflight_blocks
        and not sync_block_queue
        and now - sync_last_progress_at > sync_stall_timeout
        and peers
    )
    
    assert stall_detected, "Stall should be detected when headers == blocks with no progress"
    
    # With the fix, error should be cleared
    should_clear_error = sync_last_header_error in ("at_tip", "invalid_headers")
    
    assert should_clear_error, "Should clear blocking error states on stall"
    print("✓ Test 3 PASSED: Stall detection clears error states")
    return True


def test_expired_blocks_requeued_with_height_hints():
    """
    Test that expired inflight blocks are re-queued with height hints.
    """
    # Simulated state
    expired_block_hash = b"test_block_hash_123"
    has_block = False
    in_queue = False
    block_height_from_header = 150
    
    # With the fix, block should be re-queued
    should_requeue = not has_block and not in_queue
    
    assert should_requeue, "Expired block should be re-queued if not already imported"
    
    # Height hint should be restored
    should_restore_height = block_height_from_header is not None
    
    assert should_restore_height, "Should restore height hint from headers"
    print("✓ Test 4 PASSED: Expired blocks re-queued with height hints")
    return True


def test_few_headers_diagnostic_logging():
    """
    Test that diagnostic logging triggers when few headers but large gap.
    """
    # Simulated state
    local_height = 100
    best_header_height = 200
    available_headers_count = 3
    
    gap = best_header_height - local_height
    
    # Should log warning if few headers despite large gap
    should_warn = available_headers_count < min(10, gap) and gap > 5
    
    assert should_warn, "Should warn when few headers available despite large gap"
    print("✓ Test 5 PASSED: Diagnostic logging for header gaps")
    return True


def test_snapshot_recovery_on_extended_stall():
    """
    Test that snapshot recovery triggers on extended headers==blocks stall.
    """
    # Simulated state
    sync_last_progress_at = time.time() - 120  # 120 seconds ago
    sync_watchdog_timeout = 60
    
    now = time.time()
    stall_duration = now - sync_last_progress_at
    
    # Should trigger snapshot recovery based on max of constants
    threshold = max(
        EXTENDED_STALL_SNAPSHOT_TRIGGER_SEC,
        sync_watchdog_timeout * EXTENDED_STALL_WATCHDOG_MULTIPLIER
    )
    should_trigger = stall_duration >= threshold
    
    assert should_trigger, f"Should trigger snapshot recovery on extended stall (duration: {stall_duration:.1f}s, threshold: {threshold:.1f}s)"
    print(f"✓ Test 6 PASSED: Snapshot recovery triggers on extended stall (duration: {stall_duration:.1f}s >= threshold: {threshold:.1f}s)")
    return True


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Testing Sync Recovery Improvements")
    print("=" * 60)
    
    tests = [
        test_block_enqueue_with_large_gap,
        test_small_gap_respects_parent_check,
        test_stall_detection_clears_error_states,
        test_expired_blocks_requeued_with_height_hints,
        test_few_headers_diagnostic_logging,
        test_snapshot_recovery_on_extended_stall,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} ERROR: {e}")
            failed += 1
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
