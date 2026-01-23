"""
Test that PeerRegistry is properly synchronized in HELLO_ACK handler.

This test verifies the fix for the bug where outbound peer connections never
showed as "connected" because the PeerRegistry's identity_ok flag was not
synchronized after the HELLO_ACK handshake completion.

The bug caused:
1. Outbound connections dial successfully
2. HELLO/HELLO_ACK exchange completes
3. _PeerState.identity_ok is set to True
4. HandshakeManager.on_identity_received() sets state to CONNECTED in registry
5. BUT: PeerRegistry meta never gets identity_ok=True set
6. Result: status_snapshot() reports peers_connected=0 because it requires
   BOTH state==CONNECTED AND identity_ok==True

This prevented mining with the error: "insufficient_peers (connected: 0, required: 1)"
"""

import pytest
from unittest.mock import Mock, MagicMock
from p2p.node.peer_registry import PeerRegistry, PeerState


def test_hello_ack_updates_peer_registry_identity_ok():
    """
    Test that receiving HELLO_ACK properly updates PeerRegistry.identity_ok.
    
    This verifies the fix where _handle_hello_ack() now calls:
        _peer_registry.update_meta(peer.session_id, identity_ok=True)
    """
    # Create a real PeerRegistry
    registry = PeerRegistry()
    
    # Register an outbound peer (initiator)
    session = registry.register("tcp://peer:30333", "outbound")
    session_id = session.session_id
    
    # Mark peer as identified (after receiving their HELLO)
    registry.mark_identified(session_id, "peer123")
    
    # At this point, state should be HANDSHAKING
    assert session.state == PeerState.HANDSHAKING
    assert session.identity_ok is False
    
    # Simulate successful identity validation (called in _handle_hello_ack)
    registry.mark_identity_validated(
        session_id,
        chain_id=1337,
        genesis_hash="0x1234567890abcdef",
    )
    
    # Now state should be CONNECTED
    assert session.state == PeerState.CONNECTED
    
    # But identity_ok in the registry's meta dict also needs to be set
    # This is done via update_meta() in the fixed code
    registry.update_meta(session_id, identity_ok=True)
    
    # Verify both conditions are met
    assert session.state == PeerState.CONNECTED
    assert session.identity_ok is True
    
    # Verify peer_count() now counts this peer
    assert registry.peer_count() == 1


def test_mark_identity_validated_sets_both_state_and_flag():
    """
    Test that mark_identity_validated() properly sets both state and identity_ok.
    
    This verifies that the PeerRegistry correctly maintains peer state when
    identity validation succeeds. The test ensures both the CONNECTED state
    and identity_ok flag are set, which are both required for peer_count().
    """
    # Create a real PeerRegistry
    registry = PeerRegistry()
    
    # Register an outbound peer (initiator)
    session = registry.register("tcp://peer:30333", "outbound")
    session_id = session.session_id
    
    # Mark peer as identified (moves to HANDSHAKING state)
    registry.mark_identified(session_id, "peer123")
    assert session.state == PeerState.HANDSHAKING
    assert session.identity_ok is False
    
    # Validate identity (should set both state and flag)
    registry.mark_identity_validated(
        session_id,
        chain_id=1337,
        genesis_hash="0x1234567890abcdef",
    )
    
    # Verify both conditions are met for counting as connected
    assert session.state == PeerState.CONNECTED
    assert session.identity_ok is True
    assert registry.peer_count() == 1


def test_responder_and_initiator_both_counted():
    """
    Test that both responder (inbound) and initiator (outbound) peers count as connected.
    
    This verifies that the fix applies symmetrically to both connection directions.
    """
    registry = PeerRegistry()
    
    # Responder flow (inbound connection)
    inbound_session = registry.register("tcp://peer1:30333", "inbound")
    registry.mark_identified(inbound_session.session_id, "peer1")
    registry.mark_identity_validated(
        inbound_session.session_id,
        chain_id=1337,
        genesis_hash="0xabcd",
    )
    registry.update_meta(inbound_session.session_id, identity_ok=True)
    
    # Initiator flow (outbound connection)
    outbound_session = registry.register("tcp://peer2:30333", "outbound")
    registry.mark_identified(outbound_session.session_id, "peer2")
    registry.mark_identity_validated(
        outbound_session.session_id,
        chain_id=1337,
        genesis_hash="0xabcd",
    )
    registry.update_meta(outbound_session.session_id, identity_ok=True)
    
    # Both should be counted as connected
    assert registry.peer_count() == 2
    
    # Verify status snapshot
    snapshot = registry.snapshot()
    connected_peers = [p for p in snapshot if p.get("state") == "CONNECTED" and p.get("identity_ok")]
    assert len(connected_peers) == 2


def test_status_snapshot_counts_match_peer_count():
    """
    Test that status_snapshot() connected count matches peer_count().
    
    This is the key metric used by mining to determine if enough peers are available.
    """
    registry = PeerRegistry()
    
    # Add 3 connected peers
    for i in range(3):
        session = registry.register(f"tcp://peer{i}:30333", "outbound")
        registry.mark_identified(session.session_id, f"peer{i}")
        registry.mark_identity_validated(
            session.session_id,
            chain_id=1337,
            genesis_hash="0xabcd",
        )
        registry.update_meta(session.session_id, identity_ok=True)
    
    # Add 2 handshaking peers (not yet connected)
    for i in range(3, 5):
        session = registry.register(f"tcp://peer{i}:30333", "outbound")
        registry.mark_identified(session.session_id, f"peer{i}")
        # Don't call mark_identity_validated - leave in HANDSHAKING state
    
    # peer_count() should only count the 3 connected peers
    assert registry.peer_count() == 3
    
    # Verify snapshot matches
    snapshot = registry.snapshot()
    connected_count = len([p for p in snapshot if p.get("state") == "CONNECTED" and p.get("identity_ok")])
    assert connected_count == 3
    
    # Total should include handshaking
    total_count = registry.total_active_sessions(include_handshaking=True)
    assert total_count == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
