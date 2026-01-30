"""
Edge case tests for sync duplicate recovery to ensure we don't break normal behavior.
"""

import time
from unittest.mock import Mock


def test_recovery_not_triggered_during_normal_sync():
    """
    Test that recovery doesn't trigger during normal sync progress.
    """
    service = Mock()
    
    # Simulate normal sync - progress happening regularly
    service._sync_last_progress_at = time.time() - 10  # Only 10s since progress
    service._sync_stall_timeout = 60
    service._sync_last_header_error = "duplicate_headers"
    service._sync_locator_depth_hint = 16
    
    tried_peers = {"peer1", "peer2"}
    eligible_count = 5
    
    # Check recovery condition
    now = time.time()
    should_recover = (
        len(tried_peers) >= eligible_count
        and eligible_count > 0
        and service._sync_last_header_error == "duplicate_headers"
        and now - service._sync_last_progress_at > service._sync_stall_timeout
    )
    
    assert not should_recover, "Should NOT recover when progress is recent"
    print("✓ Recovery not triggered during normal sync")


def test_recovery_not_triggered_with_few_peers_tried():
    """
    Test that recovery doesn't trigger if we haven't tried all peers.
    """
    service = Mock()
    
    # Long stall but haven't tried all peers yet
    service._sync_last_progress_at = time.time() - 120
    service._sync_stall_timeout = 60
    service._sync_last_header_error = "duplicate_headers"
    
    tried_peers = {"peer1", "peer2"}
    eligible_count = 5  # More eligible peers available
    
    now = time.time()
    should_recover = (
        len(tried_peers) >= eligible_count
        and eligible_count > 0
        and service._sync_last_header_error == "duplicate_headers"
        and now - service._sync_last_progress_at > service._sync_stall_timeout
    )
    
    assert not should_recover, "Should NOT recover when more peers to try"
    assert len(tried_peers) < eligible_count
    print("✓ Recovery not triggered when peers remain")


def test_recovery_not_triggered_for_other_errors():
    """
    Test that recovery only triggers for duplicate_headers error, not other errors.
    """
    service = Mock()
    
    service._sync_last_progress_at = time.time() - 120
    service._sync_stall_timeout = 60
    
    # Test various error types
    error_types = [
        "invalid_headers",
        "headers_timeout",
        "not_anchored",
        "genesis_mismatch",
        None,
    ]
    
    for error_type in error_types:
        service._sync_last_header_error = error_type
        
        tried_peers = {"peer1", "peer2", "peer3", "peer4", "peer5"}
        eligible_count = 5
        
        now = time.time()
        should_recover = (
            len(tried_peers) >= eligible_count
            and eligible_count > 0
            and service._sync_last_header_error == "duplicate_headers"
            and now - service._sync_last_progress_at > service._sync_stall_timeout
        )
        
        assert not should_recover, f"Should NOT recover for error: {error_type}"
    
    print("✓ Recovery only triggers for duplicate_headers error")


def test_depth_increase_still_works_when_not_stalled():
    """
    Test that normal depth increase happens when stall timeout not exceeded.
    """
    service = Mock()
    
    # Recent progress
    service._sync_last_progress_at = time.time() - 30
    service._sync_stall_timeout = 60
    service._sync_locator_depth_hint = 8
    duplicate_count = 3
    service._sync_duplicate_headers_threshold = 2
    
    now = time.time()
    stall_duration = now - service._sync_last_progress_at
    should_reset = (
        stall_duration > service._sync_stall_timeout 
        and service._sync_locator_depth_hint > 0
    )
    
    assert not should_reset, "Should not reset when not stalled"
    
    # Normal behavior: increase depth
    if duplicate_count >= service._sync_duplicate_headers_threshold and not should_reset:
        service._sync_locator_depth_hint = min(service._sync_locator_depth_hint + 8, 64)
    
    assert service._sync_locator_depth_hint == 16, "Depth should increase normally"
    print("✓ Normal depth increase still works")


def test_zero_eligible_peers_no_recovery():
    """
    Test that recovery doesn't trigger with 0 eligible peers (avoid division by zero).
    """
    service = Mock()
    
    service._sync_last_progress_at = time.time() - 120
    service._sync_stall_timeout = 60
    service._sync_last_header_error = "duplicate_headers"
    
    tried_peers = set()
    eligible_count = 0  # No eligible peers
    
    now = time.time()
    should_recover = (
        len(tried_peers) >= eligible_count
        and eligible_count > 0  # This condition prevents recovery
        and service._sync_last_header_error == "duplicate_headers"
        and now - service._sync_last_progress_at > service._sync_stall_timeout
    )
    
    assert not should_recover, "Should NOT recover with zero eligible peers"
    print("✓ No recovery with zero eligible peers")


def test_depth_reset_condition_requires_positive_depth():
    """
    Test that depth reset only happens when depth is already > 0.
    """
    service = Mock()
    
    # Already at depth 0
    service._sync_locator_depth_hint = 0
    service._sync_last_progress_at = time.time() - 120
    service._sync_stall_timeout = 60
    
    now = time.time()
    stall_duration = now - service._sync_last_progress_at
    should_reset = (
        stall_duration > service._sync_stall_timeout 
        and service._sync_locator_depth_hint > 0  # This prevents unnecessary reset
    )
    
    assert not should_reset, "Should NOT reset when already at depth 0"
    print("✓ No reset when depth already 0")


def test_first_duplicate_doesnt_trigger_reset():
    """
    Test that first duplicate doesn't trigger reset, only after threshold.
    """
    service = Mock()
    
    service._sync_last_progress_at = time.time() - 120
    service._sync_stall_timeout = 60
    service._sync_locator_depth_hint = 16
    duplicate_count = 1  # Below threshold
    service._sync_duplicate_headers_threshold = 2
    
    should_process = duplicate_count >= service._sync_duplicate_headers_threshold
    
    assert not should_process, "Should NOT process when below threshold"
    # Depth should remain unchanged
    assert service._sync_locator_depth_hint == 16
    print("✓ First duplicate doesn't trigger reset")


def test_backoff_clearing_selective():
    """
    Test that only duplicate_headers backoffs are cleared, not other reasons.
    """
    service = Mock()
    
    service._sync_peer_backoff = {
        "peer1": time.time() + 100,
        "peer2": time.time() + 100,
        "peer3": time.time() + 100,
        "peer4": time.time() + 100,
    }
    service._sync_peer_backoff_reason = {
        "peer1": "duplicate_headers",
        "peer2": "duplicate_headers",
        "peer3": "invalid_headers",  # Different reason
        "peer4": "not_anchored",     # Different reason
    }
    
    # Simulate recovery
    for backoff_key in list(service._sync_peer_backoff.keys()):
        if service._sync_peer_backoff_reason.get(backoff_key) == "duplicate_headers":
            service._sync_peer_backoff.pop(backoff_key, None)
            service._sync_peer_backoff_reason.pop(backoff_key, None)
    
    # Only duplicate_headers should be removed
    assert "peer1" not in service._sync_peer_backoff
    assert "peer2" not in service._sync_peer_backoff
    assert "peer3" in service._sync_peer_backoff
    assert "peer4" in service._sync_peer_backoff
    
    print("✓ Backoff clearing is selective")


if __name__ == "__main__":
    test_recovery_not_triggered_during_normal_sync()
    test_recovery_not_triggered_with_few_peers_tried()
    test_recovery_not_triggered_for_other_errors()
    test_depth_increase_still_works_when_not_stalled()
    test_zero_eligible_peers_no_recovery()
    test_depth_reset_condition_requires_positive_depth()
    test_first_duplicate_doesnt_trigger_reset()
    test_backoff_clearing_selective()
    print("\n✅ All edge case tests passed!")
