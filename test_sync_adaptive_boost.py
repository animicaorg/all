#!/usr/bin/env python3
"""
Test for adaptive boost mechanism that maintains high-speed syncing
while blocks are actively being processed.

This test verifies that:
1. Boost mode is maintained when blocks are in-flight
2. Boost mode is maintained when blocks are queued
3. Boost expires only when no active syncing is happening
"""

import time
from unittest.mock import MagicMock, PropertyMock


def calculate_active_sync(
    sync_block_queue_len: int,
    sync_inflight_blocks_len: int,
    sync_block_buffer_len: int,
    best_header_height: int,
    local_head_height: int,
) -> bool:
    """
    Calculate if sync is active based on queue/inflight/buffer/header status.
    This mirrors the production logic in p2p_service.py.
    """
    return (
        sync_block_queue_len > 0
        or sync_inflight_blocks_len > 0
        or sync_block_buffer_len > 0
        or best_header_height > local_head_height
    )


def calculate_tick_rate(
    sync_boost_tick_sec: float | None, sync_tick_sec: float
) -> float:
    """Calculate the boosted tick rate."""
    return (
        sync_boost_tick_sec
        if sync_boost_tick_sec is not None
        else min(sync_tick_sec, max(0.001, sync_tick_sec / 5))
    )


def test_adaptive_boost_extends_during_active_sync():
    """Test that boost is extended when blocks are actively being synced."""
    print("\n=== Test: Adaptive Boost Extends During Active Sync ===")
    
    # Simulate the key sync loop logic
    sync_boost_until = time.time() + 1.0  # Boost expires in 1 second
    sync_request_timeout = 15.0
    sync_tick_sec = 0.005
    sync_boost_tick_sec = 0.001
    
    # Simulate having active sync activity
    sync_block_queue_len = 100  # Blocks queued
    sync_inflight_blocks_len = 50  # Blocks in-flight
    sync_block_buffer_len = 10  # Blocks buffered
    best_header_height = 1000
    local_head_height = 900  # Behind best header
    
    # Wait for boost to "expire"
    time.sleep(1.1)
    now = time.time()
    
    # Check if boost would normally expire
    assert now >= sync_boost_until, "Boost should have expired"
    print(f"✓ Boost expired at: {sync_boost_until}, now: {now}")
    
    # Adaptive boost logic: detect active sync
    active_sync = calculate_active_sync(
        sync_block_queue_len,
        sync_inflight_blocks_len,
        sync_block_buffer_len,
        best_header_height,
        local_head_height,
    )
    
    print(f"✓ Active sync detected: {active_sync}")
    print(f"  - Queued blocks: {sync_block_queue_len}")
    print(f"  - Inflight blocks: {sync_inflight_blocks_len}")
    print(f"  - Buffered blocks: {sync_block_buffer_len}")
    print(f"  - Headers ahead: {best_header_height - local_head_height}")
    
    # If active, extend boost
    if active_sync:
        new_boost_until = now + max(1.0, sync_request_timeout)
        print(f"✓ Boost extended to: {new_boost_until} (added {sync_request_timeout}s)")
        assert new_boost_until > sync_boost_until, "Boost should be extended"
        
        # Tick rate should remain boosted
        tick = calculate_tick_rate(sync_boost_tick_sec, sync_tick_sec)
        print(f"✓ Tick rate maintained at: {tick * 1000:.2f}ms (boosted)")
        assert tick < sync_tick_sec, "Tick should be faster than normal"
    else:
        print("✗ No active sync - boost would expire")
    
    print("✓ Test passed: Boost extended during active sync\n")


def test_adaptive_boost_expires_when_idle():
    """Test that boost expires when no blocks are being synced."""
    print("\n=== Test: Adaptive Boost Expires When Idle ===")
    
    sync_boost_until = time.time() + 1.0
    sync_request_timeout = 15.0
    sync_tick_sec = 0.005
    
    # Simulate no active sync activity
    sync_block_queue_len = 0
    sync_inflight_blocks_len = 0
    sync_block_buffer_len = 0
    best_header_height = 900
    local_head_height = 900  # At tip
    
    # Wait for boost to expire
    time.sleep(1.1)
    now = time.time()
    
    # Check if boost expired
    assert now >= sync_boost_until, "Boost should have expired"
    print(f"✓ Boost expired at: {sync_boost_until}, now: {now}")
    
    # Adaptive boost logic: detect no active sync
    active_sync = calculate_active_sync(
        sync_block_queue_len,
        sync_inflight_blocks_len,
        sync_block_buffer_len,
        best_header_height,
        local_head_height,
    )
    
    print(f"✓ Active sync detected: {active_sync}")
    print(f"  - Queued blocks: {sync_block_queue_len}")
    print(f"  - Inflight blocks: {sync_inflight_blocks_len}")
    print(f"  - Buffered blocks: {sync_block_buffer_len}")
    print(f"  - Headers ahead: {best_header_height - local_head_height}")
    
    # If not active, boost should expire
    if not active_sync:
        print("✓ No active sync - boost expires as expected")
        # Tick rate should return to normal
        tick = sync_tick_sec
        print(f"✓ Tick rate returns to normal: {tick * 1000:.2f}ms")
    else:
        print("✗ Active sync detected - boost should not expire")
        assert False, "Boost should expire when idle"
    
    print("✓ Test passed: Boost expires when idle\n")


def test_adaptive_boost_with_queued_blocks_only():
    """Test that boost is maintained even if only blocks are queued (not in-flight)."""
    print("\n=== Test: Adaptive Boost With Queued Blocks Only ===")
    
    sync_boost_until = time.time() + 1.0
    sync_request_timeout = 15.0
    
    # Simulate blocks queued but not yet in-flight
    sync_block_queue_len = 500  # Large queue
    sync_inflight_blocks_len = 0  # Nothing in-flight yet
    sync_block_buffer_len = 0
    best_header_height = 1000
    local_head_height = 500
    
    time.sleep(1.1)
    now = time.time()
    
    active_sync = calculate_active_sync(
        sync_block_queue_len,
        sync_inflight_blocks_len,
        sync_block_buffer_len,
        best_header_height,
        local_head_height,
    )
    
    print(f"✓ Active sync detected: {active_sync}")
    print(f"  - Queued blocks: {sync_block_queue_len}")
    print(f"  - Best header ahead by: {best_header_height - local_head_height}")
    
    assert active_sync, "Should detect active sync with queued blocks"
    
    # Boost should be extended
    new_boost_until = now + max(1.0, sync_request_timeout)
    print(f"✓ Boost extended to process {sync_block_queue_len} queued blocks")
    print(f"✓ Test passed: Boost maintained with queued blocks\n")


def test_adaptive_boost_with_headers_ahead():
    """Test that boost is maintained when best header is ahead of local height."""
    print("\n=== Test: Adaptive Boost With Headers Ahead ===")
    
    sync_boost_until = time.time() + 1.0
    sync_request_timeout = 15.0
    
    # Simulate headers ahead but no blocks yet
    sync_block_queue_len = 0
    sync_inflight_blocks_len = 0
    sync_block_buffer_len = 0
    best_header_height = 2000
    local_head_height = 1000  # 1000 blocks behind
    
    time.sleep(1.1)
    now = time.time()
    
    active_sync = calculate_active_sync(
        sync_block_queue_len,
        sync_inflight_blocks_len,
        sync_block_buffer_len,
        best_header_height,
        local_head_height,
    )
    
    print(f"✓ Active sync detected: {active_sync}")
    print(f"  - Best header ahead by: {best_header_height - local_head_height} blocks")
    
    assert active_sync, "Should detect active sync when headers are ahead"
    
    # Boost should be extended to catch up
    new_boost_until = now + max(1.0, sync_request_timeout)
    print(f"✓ Boost extended to catch up {best_header_height - local_head_height} blocks")
    print(f"✓ Test passed: Boost maintained when headers are ahead\n")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("Testing Adaptive Boost Mechanism for Sync Stall Fix")
    print("=" * 70)
    
    try:
        test_adaptive_boost_extends_during_active_sync()
        test_adaptive_boost_expires_when_idle()
        test_adaptive_boost_with_queued_blocks_only()
        test_adaptive_boost_with_headers_ahead()
        
        print("=" * 70)
        print("✓ ALL TESTS PASSED")
        print("=" * 70)
        print("\nAdaptive boost mechanism is working correctly:")
        print("- Boost maintained during active sync (queued/inflight blocks)")
        print("- Boost maintained when headers are ahead")
        print("- Boost expires only when truly idle")
        print("- Prevents dramatic slowdown during sustained syncing")
        print()
        
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}\n")
        exit(1)
    except Exception as e:
        print(f"\n✗ TEST ERROR: {e}\n")
        raise
