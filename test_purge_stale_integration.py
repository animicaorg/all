"""
Integration test to verify that stuck handshaking peers are purged by the watchdog loop.

This test demonstrates the fix for the issue where peers stuck in handshaking state
prevent mining from proceeding.
"""

import time
import pytest
from p2p.node.peer_registry import PeerRegistry


def test_purge_stale_removes_handshaking_peers():
    """
    Test that purge_stale() correctly removes peers stuck in handshaking state.
    
    This simulates the scenario from the problem statement:
    - A peer is stuck with "handshaking: 1" status
    - The peer never completes the handshake
    - After the timeout period, purge_stale() should remove it
    """
    # Create registry with a short timeout for testing
    registry = PeerRegistry(handshake_timeout_s=0.1)
    
    # Simulate a peer starting handshake (like in the error message)
    session = registry.register("tcp://82.66.161.84:41596", "outbound")
    
    # Initially, we have 1 session in handshaking state (no peer_id yet)
    assert registry.total_active_sessions(include_handshaking=True) == 1
    assert registry.peer_count() == 0  # Not fully connected yet
    
    # Simulate the session staying stuck in handshaking
    # (In real scenario, this happens due to dial timeout or network issues)
    time.sleep(0.15)  # Wait longer than handshake_timeout_s
    
    # Before purge_stale, peer is still there
    assert registry.total_active_sessions(include_handshaking=True) == 1
    
    # Run purge_stale (this is what the watchdog loop now does)
    purged = registry.purge_stale()
    
    # Verify the stuck peer was purged
    assert len(purged) == 1
    assert session.session_id in purged
    
    # Verify the peer is gone
    assert registry.total_active_sessions(include_handshaking=True) == 0
    assert registry.peer_count() == 0
    
    print("✓ Test passed: Stuck handshaking peer was successfully purged")


def test_purge_stale_does_not_remove_connected_peers():
    """
    Test that purge_stale() does NOT remove peers that completed handshake.
    """
    registry = PeerRegistry(handshake_timeout_s=0.1)
    
    # Register and complete handshake
    session = registry.register("tcp://192.168.1.1:30333", "outbound")
    registry.mark_identified(session.session_id, "peer1")
    registry.mark_identity_validated(
        session.session_id,
        chain_id=1,
        genesis_hash="0" * 64
    )
    
    # Verify peer is connected
    assert registry.peer_count() == 1
    
    # Wait longer than timeout
    time.sleep(0.15)
    
    # Run purge_stale
    purged = registry.purge_stale()
    
    # Verify connected peer was NOT purged
    assert len(purged) == 0
    assert registry.peer_count() == 1
    
    print("✓ Test passed: Connected peer was not purged")


def test_scenario_from_problem_statement():
    """
    Test the exact scenario from the problem statement:
    - Connected: 0
    - Handshaking: 1
    - Required: 1
    
    After timeout, handshaking peer should be purged, allowing retry.
    """
    registry = PeerRegistry(handshake_timeout_s=0.1)
    
    # Simulate the exact state from error message
    session = registry.register("tcp://82.66.161.84:41596", "outbound")
    
    # Initial state matches error message
    peers_connected = registry.peer_count()
    peers_total = registry.total_active_sessions(include_handshaking=True)
    peers_handshaking = peers_total - peers_connected
    
    assert peers_connected == 0, "Should have 0 connected peers"
    assert peers_handshaking == 1, "Should have 1 handshaking peer"
    assert peers_total == 1, "Should have 1 total peer"
    
    print(f"Before purge: connected={peers_connected}, handshaking={peers_handshaking}, total={peers_total}")
    
    # Wait for timeout
    time.sleep(0.15)
    
    # Purge stale handshaking peers (this is the fix)
    purged = registry.purge_stale()
    
    # After purge, stuck peer is gone
    peers_connected = registry.peer_count()
    peers_total = registry.total_active_sessions(include_handshaking=True)
    peers_handshaking = peers_total - peers_connected
    
    assert peers_connected == 0, "Should still have 0 connected peers"
    assert peers_handshaking == 0, "Should now have 0 handshaking peers (purged)"
    assert peers_total == 0, "Should now have 0 total peers"
    assert len(purged) == 1, "Should have purged 1 peer"
    
    print(f"After purge: connected={peers_connected}, handshaking={peers_handshaking}, total={peers_total}")
    print("✓ Test passed: Stuck peer scenario resolved - mining can now proceed or retry")
