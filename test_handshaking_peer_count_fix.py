#!/usr/bin/env python3
"""
Test to verify handshaking peers are counted for sync to progress.

This test validates the fix for the bug where sync would never progress
because peers in handshaking state (peer_id=None) were not counted in
peers_total, causing sync to wait indefinitely for "connected" peers.
"""

from p2p.node.peer_registry import PeerRegistry


def test_handshaking_peers_counted_in_total_active_sessions():
    """Test that handshaking peers are included in total_active_sessions count."""
    registry = PeerRegistry()
    
    # Register an outbound peer (handshaking - no peer_id yet)
    session1 = registry.register("tcp://192.168.1.1:30333", "outbound")
    assert session1.peer_id is None, "New peer should not have peer_id yet"
    
    # Check counts
    assert registry.total_active_sessions(include_handshaking=True) == 1, \
        "Should count handshaking peer"
    assert registry.total_active_sessions(include_handshaking=False) == 0, \
        "Should not count handshaking peer when include_handshaking=False"
    assert registry.peer_count() == 0, \
        "peer_count() should not count handshaking peer (needs identity_ok)"
    
    # Complete handshake by setting peer_id and identity_ok
    registry.mark_identified(session1.session_id, "peer1")
    registry.update_meta(session1.session_id, identity_ok=True)
    
    # Check counts after handshake
    assert registry.total_active_sessions(include_handshaking=True) == 1, \
        "Should still count peer after handshake"
    assert registry.total_active_sessions(include_handshaking=False) == 1, \
        "Should count peer with peer_id when include_handshaking=False"
    assert registry.peer_count() == 1, \
        "peer_count() should count validated peer"
    
    print("✓ Test passed: Handshaking peers are correctly counted")


def test_multiple_handshaking_peers():
    """Test counting multiple peers in various states."""
    registry = PeerRegistry()
    
    # Register 3 peers in handshaking state
    session1 = registry.register("tcp://192.168.1.1:30333", "outbound")
    session2 = registry.register("tcp://192.168.1.2:30333", "outbound")
    session3 = registry.register("tcp://192.168.1.3:30333", "inbound")
    
    # All handshaking
    assert registry.total_active_sessions(include_handshaking=True) == 3, \
        "Should count all 3 handshaking peers"
    assert registry.peer_count() == 0, \
        "peer_count() should be 0 (none validated yet)"
    
    # Complete handshake for 1 peer
    registry.mark_identified(session1.session_id, "peer1")
    registry.update_meta(session1.session_id, identity_ok=True)
    
    assert registry.total_active_sessions(include_handshaking=True) == 3, \
        "Should count all 3 peers (1 validated, 2 handshaking)"
    assert registry.peer_count() == 1, \
        "peer_count() should count 1 validated peer"
    
    # Complete handshake for another peer
    registry.mark_identified(session2.session_id, "peer2")
    registry.update_meta(session2.session_id, identity_ok=True)
    
    assert registry.total_active_sessions(include_handshaking=True) == 3, \
        "Should count all 3 peers (2 validated, 1 handshaking)"
    assert registry.peer_count() == 2, \
        "peer_count() should count 2 validated peers"
    
    print("✓ Test passed: Multiple peers in various states counted correctly")


def test_sync_should_see_handshaking_peers():
    """
    Test the scenario from the bug report: sync stuck at 0%
    because handshaking peers were not counted.
    """
    registry = PeerRegistry()
    
    # Scenario: Node has 1 peer connecting (handshaking)
    # This is exactly what was shown in the bug report:
    # "1. (handshaking) (144.126.133.21:30333) [handshaking] outbound"
    session = registry.register("tcp://144.126.133.21:30333", "outbound")
    
    # Before fix: peers_total would be 0 (peer not counted)
    # After fix: peers_total should be 1 (handshaking peer counted)
    peers_total = registry.total_active_sessions(include_handshaking=True)
    
    assert peers_total > 0, \
        "SYNC BUG: peers_total is 0 despite handshaking peer present! " \
        "Sync will report 'no_peers_connected' and never progress."
    
    assert peers_total == 1, \
        f"Expected peers_total=1 for 1 handshaking peer, got {peers_total}"
    
    print("✓ Test passed: Sync will see handshaking peer and can progress")
    print(f"  peers_total={peers_total} (handshaking peer counted)")


if __name__ == "__main__":
    test_handshaking_peers_counted_in_total_active_sessions()
    test_multiple_handshaking_peers()
    test_sync_should_see_handshaking_peers()
    print("\n✓ All tests passed!")
    print("Fix verified: Handshaking peers are now counted in peers_total")
    print("Sync will no longer be stuck at 0% with 'no_peers_connected'")
