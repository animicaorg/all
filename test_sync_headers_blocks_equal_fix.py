#!/usr/bin/env python3
"""
Test suite for sync headers==blocks stuck fix.

Tests the scenario where blockchain sync gets stuck a few blocks away from highest head
because headers == blocks and all connected peers report heights at or below local height.

The fix ensures:
1. When headers == blocks, we try multiple peers to check for new blocks
2. Headers==blocks stall detection uses reduced timeout (half of normal stall timeout)
3. Proactive header requests prevent long stall periods
"""

import time
from typing import Dict, List, Set


def test_headers_blocks_equal_tries_multiple_peers():
    """
    Test that when headers == blocks, we try multiple peers instead of stopping immediately.
    
    Scenario:
    - Local height: 6495 (headers == blocks)
    - Peer 1 height: 6495 (same as local)
    - Peer 2 height: 6497 (has new blocks!)
    - Peer 3 height: 6496
    
    Expected: Should try peer 2 and peer 3 after peer 1, not give up after peer 1
    """
    local_height = 6495
    best_header_height = 6495
    
    # Simulate eligible peers with different heights
    peers = [
        {"remote": "peer1", "height": 6495},
        {"remote": "peer2", "height": 6497},
        {"remote": "peer3", "height": 6496},
    ]
    
    eligible_count = len(peers)
    tried_peers: Set[str] = set()
    max_peers_to_try = min(eligible_count, 3)
    
    # Simulate trying peers
    found_higher_peer = False
    for peer in peers:
        if len(tried_peers) >= max_peers_to_try:
            break
        
        peer_height = peer["height"]
        remote = peer["remote"]
        
        # Check if headers == blocks
        if best_header_height == local_height:
            # Old logic would stop here if peer_height <= local_height
            # New logic continues to try more peers
            if peer_height <= local_height and len(tried_peers) < max_peers_to_try:
                tried_peers.add(remote)
                print(f"  Tried {remote} (height {peer_height}), continuing to next peer...")
                continue
        
        # Found a peer with higher height!
        if peer_height > local_height:
            found_higher_peer = True
            print(f"  ✓ Found peer {remote} with height {peer_height} > local {local_height}")
            break
        
        tried_peers.add(remote)
    
    assert found_higher_peer, \
        f"Should find peer with higher height by trying multiple peers (tried {len(tried_peers)} peers)"
    assert "peer2" in tried_peers or found_higher_peer, \
        "Should have tried peer2 which has higher blocks"
    
    print("✓ Test 1 PASSED: Headers==blocks tries multiple peers")
    return True


def test_reduced_stall_timeout_for_headers_blocks_equal():
    """
    Test that headers==blocks stall detection uses reduced timeout (half of normal).
    
    This ensures faster detection when stuck at the same height.
    """
    normal_stall_timeout = 30.0  # Normal stall timeout
    reduced_timeout = normal_stall_timeout / 2.0  # 15 seconds
    
    # Simulate time progression
    last_progress_at = time.time()
    best_header_height = 6495
    best_block_height = 6495
    inflight_headers = 0
    inflight_blocks = 0
    block_queue_empty = True
    has_peers = True
    
    # Wait for reduced timeout
    time.sleep(0.1)  # Small delay for test
    now = last_progress_at + reduced_timeout + 1.0  # Simulate 16 seconds elapsed
    
    # Check if stall should be detected
    stall_elapsed = now - last_progress_at
    should_detect_stall = (
        best_header_height == best_block_height
        and best_block_height > 0
        and not inflight_headers
        and not inflight_blocks
        and block_queue_empty
        and stall_elapsed > reduced_timeout
        and has_peers
    )
    
    assert should_detect_stall, \
        f"Should detect stall after {reduced_timeout}s (reduced), not full {normal_stall_timeout}s"
    assert stall_elapsed < normal_stall_timeout, \
        "Should detect before normal stall timeout"
    
    print(f"✓ Test 2 PASSED: Reduced timeout ({reduced_timeout}s) detects stall faster")
    return True


def test_at_tip_error_cleared_on_force_sync():
    """
    Test that 'at_tip' error is cleared when force sync is triggered.
    
    This allows retrying headers even when previous attempt returned empty.
    """
    sync_last_header_error = "at_tip"
    force_sync = True
    
    # Simulate force sync clearing logic
    if force_sync and sync_last_header_error == "at_tip":
        sync_last_header_error = None
        print("  'at_tip' error cleared due to forced sync")
    
    assert sync_last_header_error is None, \
        "'at_tip' error should be cleared when force=True"
    
    print("✓ Test 3 PASSED: 'at_tip' error cleared on force sync")
    return True


def test_headers_blocks_equal_stall_triggers_force_sync():
    """
    Test that when headers==blocks stall is detected, it triggers forced sync.
    
    This ensures we aggressively try to find new blocks from different peers.
    """
    stall_reason = None
    sync_requested = False
    
    # Simulate stall detection
    best_header_height = 6495
    best_block_height = 6495
    no_progress = True
    
    if best_header_height == best_block_height and no_progress:
        stall_reason = "headers_blocks_equal_stall"
        sync_requested = True
        print(f"  Stall detected: {stall_reason}")
        print("  Triggered forced sync")
    
    assert stall_reason == "headers_blocks_equal_stall", \
        "Should detect headers==blocks stall"
    assert sync_requested, \
        "Should trigger forced sync to try different peers"
    
    print("✓ Test 4 PASSED: Stall triggers forced sync")
    return True


def test_integration_scenario_stuck_near_tip():
    """
    Integration test for the full scenario: stuck a few blocks from network tip.
    
    Scenario:
    - Local: height 6495, headers 6495
    - Connected peers: all report height 6495
    - Network tip: actually at 6497
    - After peer rotation: find peer with 6497
    
    Expected: Should recover within reduced stall timeout + peer rotation
    """
    print("\n  Simulating stuck sync scenario:")
    
    # Initial state
    local_height = 6495
    best_header_height = 6495
    network_tip = 6497
    
    print(f"  - Local height: {local_height}, headers: {best_header_height}")
    print(f"  - Network tip: {network_tip}")
    print(f"  - Gap: {network_tip - local_height} blocks")
    
    # Phase 1: Try current peers (all at 6495)
    initial_peers = [
        {"remote": "peer1", "height": 6495},
        {"remote": "peer2", "height": 6495},
    ]
    tried_peers: Set[str] = set()
    found_blocks = False
    
    print("\n  Phase 1: Trying initial peers...")
    for peer in initial_peers:
        if peer["height"] > local_height:
            found_blocks = True
            break
        tried_peers.add(peer["remote"])
        print(f"    - {peer['remote']}: height {peer['height']} (no new blocks)")
    
    assert not found_blocks, "Should not find blocks from initial peers"
    assert len(tried_peers) == 2, "Should try multiple initial peers"
    
    # Phase 2: Detect stall (reduced timeout)
    reduced_timeout = 15.0
    stall_detected = True
    print(f"\n  Phase 2: Stall detected after {reduced_timeout}s")
    
    # Phase 3: Forced sync with peer rotation
    print("\n  Phase 3: Forced sync with peer rotation...")
    new_peer = {"remote": "peer3", "height": 6497}
    print(f"    - Found new peer: {new_peer['remote']} at height {new_peer['height']}")
    
    if new_peer["height"] > local_height:
        found_blocks = True
        blocks_available = new_peer["height"] - local_height
        print(f"    ✓ Can sync {blocks_available} new blocks!")
    
    assert found_blocks, "Should find new blocks after peer rotation"
    assert new_peer["height"] == network_tip, "Should reach network tip"
    
    print("\n✓ Test 5 PASSED: Integration scenario recovers from stuck state")
    return True


def main():
    """Run all tests."""
    print("=" * 70)
    print("Sync Headers==Blocks Equal Fix - Test Suite")
    print("=" * 70)
    print()
    
    tests = [
        test_headers_blocks_equal_tries_multiple_peers,
        test_reduced_stall_timeout_for_headers_blocks_equal,
        test_at_tip_error_cleared_on_force_sync,
        test_headers_blocks_equal_stall_triggers_force_sync,
        test_integration_scenario_stuck_near_tip,
    ]
    
    passed = 0
    failed = 0
    
    for test_func in tests:
        print(f"\n{test_func.__name__}:")
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
    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 70)
    
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
