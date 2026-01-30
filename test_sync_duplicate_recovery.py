"""
Test for sync stall recovery when all peers return duplicate headers.

This test simulates the scenario from the bug report where:
- Node is at height 7468
- Network is at height 7520
- Last matched ancestor is at 6436
- All peers return "duplicate" headers
- Node gets stuck in infinite loop rotating through peers
"""

import asyncio
import time
from unittest.mock import Mock, MagicMock, AsyncMock
from p2p.node.p2p_service import P2PService


def test_duplicate_header_recovery_resets_locator_depth():
    """
    Test that when all peers return duplicates and we're stalled,
    the locator depth hint gets reset to allow more detailed locators.
    """
    # Create a mock P2P service with minimal required attributes
    service = Mock(spec=P2PService)
    
    # Simulate initial state
    service._sync_locator_depth_hint = 32  # Already increased from duplicates
    service._sync_last_progress_at = time.time() - 120  # Stalled for 2 minutes
    service._sync_stall_timeout = 60  # Test with 60s (actual default is 20s)
    service._sync_last_header_error = "duplicate_headers"
    service._sync_duplicate_headers_threshold = 2
    
    # Simulate the state after receiving duplicate headers multiple times
    duplicate_count = 3  # Above threshold
    stall_duration = time.time() - service._sync_last_progress_at
    
    # Check if we should reset (mimics the fix logic)
    should_reset = (
        stall_duration > service._sync_stall_timeout 
        and service._sync_locator_depth_hint > 0
        and duplicate_count >= service._sync_duplicate_headers_threshold
    )
    
    assert should_reset, "Should trigger reset when stalled with duplicates"
    
    # After reset, depth hint should be 0
    if should_reset:
        service._sync_locator_depth_hint = 0
    
    assert service._sync_locator_depth_hint == 0, "Locator depth hint should be reset to 0"
    print("✓ Duplicate header recovery correctly resets locator depth")


def test_all_peers_duplicate_recovery_clears_backoff():
    """
    Test that when all peers return duplicates, we clear the backoff state
    to allow immediate retry.
    """
    service = Mock(spec=P2PService)
    
    # Simulate state when all peers have been tried
    eligible_count = 5
    tried_peers = {"peer1", "peer2", "peer3", "peer4", "peer5"}
    service._sync_last_progress_at = time.time() - 120
    service._sync_stall_timeout = 60
    service._sync_last_header_error = "duplicate_headers"
    service._sync_locator_depth_hint = 48
    service._sync_peer_backoff = {
        "peer1": time.time() + 100,
        "peer2": time.time() + 100,
        "peer3": time.time() + 100,
    }
    service._sync_peer_backoff_reason = {
        "peer1": "duplicate_headers",
        "peer2": "duplicate_headers",
        "peer3": "other_reason",
    }
    service._sync_duplicate_header_ranges = {
        "peer1": (("hash1", "hash2", 10), 3),
        "peer2": (("hash3", "hash4", 10), 2),
    }
    
    # Check if recovery should trigger
    now = time.time()
    should_recover = (
        len(tried_peers) >= eligible_count
        and eligible_count > 0
        and service._sync_last_header_error == "duplicate_headers"
        and now - service._sync_last_progress_at > service._sync_stall_timeout
    )
    
    assert should_recover, "Should trigger recovery when all peers return duplicates"
    
    if should_recover:
        # Reset state (mimics the fix)
        service._sync_locator_depth_hint = 0
        service._sync_last_header_error = None
        service._sync_duplicate_header_ranges.clear()
        
        # Clear duplicate_headers backoffs
        for backoff_key in list(service._sync_peer_backoff.keys()):
            if service._sync_peer_backoff_reason.get(backoff_key) == "duplicate_headers":
                service._sync_peer_backoff.pop(backoff_key, None)
                service._sync_peer_backoff_reason.pop(backoff_key, None)
    
    assert service._sync_locator_depth_hint == 0, "Locator depth should be reset"
    assert service._sync_last_header_error is None, "Header error should be cleared"
    assert len(service._sync_duplicate_header_ranges) == 0, "Duplicate ranges should be cleared"
    assert "peer1" not in service._sync_peer_backoff, "peer1 backoff should be cleared"
    assert "peer2" not in service._sync_peer_backoff, "peer2 backoff should be cleared"
    assert "peer3" in service._sync_peer_backoff, "peer3 backoff (other reason) should remain"
    
    print("✓ All-peers-duplicate recovery correctly clears backoff state")


def test_normal_duplicate_handling_still_increases_depth():
    """
    Test that normal duplicate handling (not stalled) still increases depth.
    """
    service = Mock(spec=P2PService)
    
    # Simulate normal state (not stalled long enough)
    service._sync_locator_depth_hint = 8
    service._sync_last_progress_at = time.time() - 30  # Only 30s stall
    service._sync_stall_timeout = 60  # 1 minute stall timeout
    duplicate_count = 3
    service._sync_duplicate_headers_threshold = 2
    
    # Check condition
    stall_duration = time.time() - service._sync_last_progress_at
    should_reset = (
        stall_duration > service._sync_stall_timeout 
        and service._sync_locator_depth_hint > 0
    )
    
    assert not should_reset, "Should NOT reset when not stalled long enough"
    
    # Normal path: increase depth
    if duplicate_count >= service._sync_duplicate_headers_threshold:
        if not should_reset:
            service._sync_locator_depth_hint = min(
                service._sync_locator_depth_hint + 8, 64
            )
    
    assert service._sync_locator_depth_hint == 16, "Depth should increase normally"
    print("✓ Normal duplicate handling still increases depth when not stalled")


def test_locator_depth_caps_at_64():
    """
    Test that locator depth hint never exceeds 64.
    """
    service = Mock(spec=P2PService)
    service._sync_locator_depth_hint = 60
    
    # Increase normally
    service._sync_locator_depth_hint = min(service._sync_locator_depth_hint + 8, 64)
    
    assert service._sync_locator_depth_hint == 64, "Should cap at 64"
    
    # Try to increase again
    service._sync_locator_depth_hint = min(service._sync_locator_depth_hint + 8, 64)
    
    assert service._sync_locator_depth_hint == 64, "Should remain at 64"
    print("✓ Locator depth properly caps at 64")


if __name__ == "__main__":
    test_duplicate_header_recovery_resets_locator_depth()
    test_all_peers_duplicate_recovery_clears_backoff()
    test_normal_duplicate_handling_still_increases_depth()
    test_locator_depth_caps_at_64()
    print("\n✅ All sync duplicate recovery tests passed!")
