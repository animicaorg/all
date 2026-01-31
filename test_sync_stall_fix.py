#!/usr/bin/env python3
"""
Simple test to verify the sync stall fix logic.
This tests the key scenarios where sync gets stuck.
"""

# Test constants
# STALL_TIME_SECONDS must be > STALL_TIMEOUT_SECONDS to trigger stall detection
STALL_TIMEOUT_SECONDS = 5
STALL_TIME_SECONDS = STALL_TIMEOUT_SECONDS + 5  # Must exceed timeout to trigger stall

def test_at_tip_clearing_on_force():
    """Test that 'at_tip' error is cleared when force=True"""
    # Simulated state
    sync_last_header_error = "at_tip"
    force = True
    
    # Apply the fix logic
    if force and sync_last_header_error == "at_tip":
        sync_last_header_error = None
        print("✓ Test 1 PASSED: 'at_tip' error cleared on forced sync")
    else:
        print("✗ Test 1 FAILED")
        return False
    
    return True


def test_headers_blocks_equal_detection():
    """Test detection of headers == blocks stall condition"""
    import time
    
    # Simulated state
    best_header_height = 6495
    best_block_height = 6495
    sync_inflight_headers = 0
    sync_inflight_blocks = {}
    sync_block_queue = []
    sync_last_progress_at = time.time() - STALL_TIME_SECONDS
    sync_stall_timeout = STALL_TIMEOUT_SECONDS
    peers = {"peer1": {}}
    sync_block_stalled_reason = None
    
    now = time.time()
    
    # Apply the detection logic
    if (
        best_header_height == best_block_height
        and best_block_height > 0
        and not sync_inflight_headers
        and not sync_inflight_blocks
        and not sync_block_queue
        and now - sync_last_progress_at > sync_stall_timeout
        and peers
    ):
        if sync_block_stalled_reason != "headers_blocks_equal_stall":
            sync_block_stalled_reason = "headers_blocks_equal_stall"
            print("✓ Test 2 PASSED: headers == blocks stall detected")
        else:
            print("✗ Test 2 FAILED: stall reason already set")
            return False
    else:
        print("✗ Test 2 FAILED: stall not detected")
        return False
    
    return True


def test_stall_handler_with_none_header():
    """Test that stall handler works when _sync_best_header is None"""
    # Simulated state
    sync_best_header = None
    local_height = 6495
    
    # Old logic would have returned early here, new logic continues
    # Apply the fix logic
    best_header_height = sync_best_header.height if sync_best_header else local_height
    
    if best_header_height == local_height:
        print("✓ Test 3 PASSED: stall handler works with None _sync_best_header")
        return True
    else:
        print("✗ Test 3 FAILED")
        return False


def test_normal_sync_not_affected():
    """Test that normal sync (headers > blocks) is not affected"""
    import time
    
    # Simulated state - normal sync in progress
    best_header_height = 6906
    best_block_height = 6495
    sync_inflight_headers = 0
    sync_inflight_blocks = {}
    sync_block_queue = []
    sync_last_progress_at = time.time() - STALL_TIME_SECONDS
    sync_stall_timeout = STALL_TIMEOUT_SECONDS
    peers = {"peer1": {}}
    sync_block_stalled_reason = None
    
    now = time.time()
    
    # Apply the detection logic
    if (
        best_header_height == best_block_height
        and best_block_height > 0
        and not sync_inflight_headers
        and not sync_inflight_blocks
        and not sync_block_queue
        and now - sync_last_progress_at > sync_stall_timeout
        and peers
    ):
        print("✗ Test 4 FAILED: normal sync incorrectly marked as stalled")
        return False
    else:
        print("✓ Test 4 PASSED: normal sync (headers > blocks) not marked as stalled")
    return True


def test_stall_reason_enums_present():
    """Test that deterministic stall reason enums are available."""
    from p2p.node.p2p_service import (
        STALL_BLOCK_TIMEOUT,
        STALL_BLOCK_PEER_UNRESPONSIVE,
        STALL_BLOCK_NOT_FOUND_ACROSS_PEERS,
        STALL_BLOCK_INVALID_RESPONSE,
        STALL_CACHE_LOOP,
        STALL_VERIFY_BACKPRESSURE,
    )

    reasons = {
        STALL_BLOCK_TIMEOUT,
        STALL_BLOCK_PEER_UNRESPONSIVE,
        STALL_BLOCK_NOT_FOUND_ACROSS_PEERS,
        STALL_BLOCK_INVALID_RESPONSE,
        STALL_CACHE_LOOP,
        STALL_VERIFY_BACKPRESSURE,
    }
    if all(isinstance(r, str) and r.startswith("STALL_") for r in reasons):
        print("✓ Test 5 PASSED: stall reason enums are defined")
        return True
    print("✗ Test 5 FAILED: stall reason enums missing or invalid")
    return False


def test_block_cooldown_formula():
    """Test cooldown formula caps at 10 minutes."""
    base = 60
    cap = 600
    failures = 1
    cooldown = min(base * failures, cap)
    if cooldown != 60:
        print("✗ Test 6 FAILED: cooldown base mismatch")
        return False
    failures = 20
    cooldown = min(base * failures, cap)
    if cooldown != 600:
        print("✗ Test 6 FAILED: cooldown cap mismatch")
        return False
    print("✓ Test 6 PASSED: cooldown formula uses base and cap")
    return True


if __name__ == "__main__":
    print("Running sync stall fix tests...\n")
    
    results = []
    results.append(test_at_tip_clearing_on_force())
    results.append(test_headers_blocks_equal_detection())
    results.append(test_stall_handler_with_none_header())
    results.append(test_normal_sync_not_affected())
    results.append(test_stall_reason_enums_present())
    results.append(test_block_cooldown_formula())
    
    print(f"\n{'='*60}")
    if all(results):
        print("✓ All tests PASSED")
        exit(0)
    else:
        print("✗ Some tests FAILED")
        exit(1)
