"""
Test handshake timeout functionality.

Verifies that HandshakeManager properly enforces timeouts at both dial and handshake phases.
"""

import pytest
import time

from p2p.node.handshake import HandshakeManager
from p2p.node.peer_registry import PeerRegistry, PeerState


def test_dial_timeout():
    """Verify that sessions stuck in DIALING state timeout correctly."""
    registry = PeerRegistry(handshake_timeout_s=3.0)
    manager = HandshakeManager(
        registry,
        dial_timeout_s=2.0,
        handshake_timeout_s=5.0,
        chain_id=1,
        genesis_hash="00" * 32,
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Verify initial state
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.DIALING
    
    # Simulate passage of time - dial timeout triggers
    now = time.time() + 2.5
    timed_out = manager.check_timeouts(now=now)
    
    # Verify timeout triggered
    assert session_id in timed_out
    
    # Verify state transitioned to FAILED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.FAILED
    assert session.last_error == "dial_timeout"


def test_handshake_timeout():
    """Verify that sessions stuck in HANDSHAKING state timeout correctly."""
    registry = PeerRegistry(handshake_timeout_s=3.0)
    manager = HandshakeManager(
        registry,
        dial_timeout_s=2.0,
        handshake_timeout_s=5.0,
        chain_id=1,
        genesis_hash="00" * 32,
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Simulate Hello received (transitions to HANDSHAKING)
    manager.on_hello_received(
        session_id,
        peer_id="peer1" * 8,  # 32-byte hex
        version="2",
        agent="test-peer/1.0",
    )
    
    # Verify state is now HANDSHAKING
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.HANDSHAKING
    
    # Simulate passage of time - handshake timeout triggers
    now = time.time() + 6.0
    timed_out = manager.check_timeouts(now=now)
    
    # Verify timeout triggered
    assert session_id in timed_out
    
    # Verify state transitioned to FAILED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.FAILED
    assert session.last_error == "handshake_timeout"


def test_no_timeout_for_completed_handshake():
    """Verify that CONNECTED sessions are not timed out."""
    registry = PeerRegistry(handshake_timeout_s=3.0)
    manager = HandshakeManager(
        registry,
        dial_timeout_s=2.0,
        handshake_timeout_s=5.0,
        chain_id=1,
        genesis_hash="00" * 32,
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Complete handshake flow
    manager.on_hello_received(
        session_id,
        peer_id="peer1" * 8,
        version="2",
        agent="test-peer/1.0",
    )
    
    success, error = manager.on_identity_received(
        session_id,
        chain_id=1,
        genesis_hash="00" * 32,
    )
    
    assert success
    assert error is None
    
    # Verify state is CONNECTED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.CONNECTED
    
    # Simulate passage of time - way past timeout
    now = time.time() + 100.0
    timed_out = manager.check_timeouts(now=now)
    
    # Verify no timeout triggered
    assert session_id not in timed_out
    
    # Verify state still CONNECTED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.CONNECTED


def test_timeout_cleanup():
    """Verify that timed out sessions are cleaned up from handshake tracking."""
    registry = PeerRegistry(handshake_timeout_s=3.0)
    manager = HandshakeManager(
        registry,
        dial_timeout_s=1.0,
        handshake_timeout_s=3.0,
        chain_id=1,
        genesis_hash="00" * 32,
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Verify handshake session exists
    assert manager.get_session(session_id) is not None
    assert manager.active_handshakes() == 1
    
    # Trigger timeout
    now = time.time() + 2.0
    timed_out = manager.check_timeouts(now=now)
    assert session_id in timed_out
    
    # Run check_timeouts again to trigger cleanup
    manager.check_timeouts(now=now + 1.0)
    
    # Verify handshake session was cleaned up
    assert manager.active_handshakes() == 0


def test_multiple_timeout_types():
    """Verify that dial and handshake timeouts work correctly for different peers."""
    registry = PeerRegistry(handshake_timeout_s=10.0)
    manager = HandshakeManager(
        registry,
        dial_timeout_s=2.0,
        handshake_timeout_s=5.0,
        chain_id=1,
        genesis_hash="00" * 32,
    )
    
    # Start two handshakes
    session1 = manager.start_handshake("tcp://peer1:8000", "outbound")
    session2 = manager.start_handshake("tcp://peer2:8000", "outbound")
    
    # Advance session2 to HANDSHAKING
    manager.on_hello_received(
        session2,
        peer_id="peer2" * 8,
        version="2",
        agent="test-peer/1.0",
    )
    
    # Verify states
    assert registry._sessions[session1].state == PeerState.DIALING
    assert registry._sessions[session2].state == PeerState.HANDSHAKING
    
    # Trigger dial timeout for session1 only
    now = time.time() + 2.5
    timed_out = manager.check_timeouts(now=now)
    
    assert session1 in timed_out
    assert session2 not in timed_out
    assert registry._sessions[session1].state == PeerState.FAILED
    assert registry._sessions[session2].state == PeerState.HANDSHAKING
    
    # Trigger handshake timeout for session2
    now = time.time() + 6.0
    timed_out = manager.check_timeouts(now=now)
    
    assert session2 in timed_out
    assert registry._sessions[session2].state == PeerState.FAILED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
