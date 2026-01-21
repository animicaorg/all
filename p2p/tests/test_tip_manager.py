"""
Test TipManager functionality.

Verifies that TipManager correctly:
- Polls peers for tip updates
- Stores received tips
- Tracks tip freshness
- Computes best tip across the network
"""

import pytest
import time

from p2p.node.tip_manager import TipManager
from p2p.node.peer_registry import PeerRegistry, PeerState


def test_tip_update():
    """Verify that tip updates are stored correctly."""
    registry = PeerRegistry()
    manager = TipManager(registry, poll_interval_s=30.0)
    
    # Register and connect a peer
    session = registry.register("tcp://peer1:8000", "outbound")
    session_id = session.session_id
    registry.mark_identified(session_id, "peer1" * 8)
    registry.mark_identity_validated(session_id, chain_id=1, genesis_hash="00" * 32)
    
    # Update tip
    manager.on_tip_received(
        session_id,
        height=100,
        hash_hex="abc123" * 10 + "abcd",
        tip_time=time.time(),
    )
    
    # Verify tip stored in registry
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.tip_height == 100
    assert session.tip_hash == "abc123" * 10 + "abcd"
    assert session.tip_updated_at is not None


def test_poll_stale_tips():
    """Verify that peers with stale tips are identified for polling."""
    registry = PeerRegistry()
    manager = TipManager(registry, poll_interval_s=30.0)
    
    # Register and connect a peer
    session = registry.register("tcp://peer1:8000", "outbound")
    session_id = session.session_id
    registry.mark_identified(session_id, "peer1" * 8)
    registry.mark_identity_validated(session_id, chain_id=1, genesis_hash="00" * 32)
    
    # Update tip
    now = time.time()
    manager.on_tip_received(session_id, height=100, hash_hex="00" * 32, tip_time=now)
    
    # Immediately check - should not need polling (tip is fresh)
    to_poll = manager.poll_peer_tips(now=now + 1.0)
    assert session_id not in to_poll
    
    # Check after poll interval - should need polling
    to_poll = manager.poll_peer_tips(now=now + 35.0)
    assert session_id in to_poll


def test_poll_only_connected_peers():
    """Verify that only CONNECTED peers with validated identity are polled."""
    registry = PeerRegistry()
    manager = TipManager(registry, poll_interval_s=30.0)
    
    # Register peer in DIALING state
    session1 = registry.register("tcp://peer1:8000", "outbound")
    session1_id = session1.session_id
    
    # Register peer in HANDSHAKING state
    session2 = registry.register("tcp://peer2:8000", "outbound")
    session2_id = session2.session_id
    registry.mark_identified(session2_id, "peer2" * 8)
    
    # Register peer in CONNECTED state
    session3 = registry.register("tcp://peer3:8000", "outbound")
    session3_id = session3.session_id
    registry.mark_identified(session3_id, "peer3" * 8)
    registry.mark_identity_validated(session3_id, chain_id=1, genesis_hash="00" * 32)
    
    # Check which peers need polling
    now = time.time() + 60.0  # Well past poll interval
    to_poll = manager.poll_peer_tips(now=now)
    
    # Only peer3 (CONNECTED) should be polled
    assert session1_id not in to_poll
    assert session2_id not in to_poll
    assert session3_id in to_poll


def test_get_best_tip():
    """Verify that best tip is computed correctly from multiple peers."""
    registry = PeerRegistry()
    manager = TipManager(registry, freshness_window_s=600.0)
    
    # Register and connect three peers
    peers = []
    for i in range(3):
        session = registry.register(f"tcp://peer{i}:8000", "outbound")
        session_id = session.session_id
        registry.mark_identified(session_id, f"peer{i}" * 8)
        registry.mark_identity_validated(session_id, chain_id=1, genesis_hash="00" * 32)
        peers.append(session_id)
    
    # Update tips with different heights
    now = time.time()
    manager.on_tip_received(peers[0], height=100, hash_hex="00" * 32, tip_time=now)
    manager.on_tip_received(peers[1], height=150, hash_hex="11" * 32, tip_time=now)
    manager.on_tip_received(peers[2], height=120, hash_hex="22" * 32, tip_time=now)
    
    # Get best tip
    height, hash_hex, peer_id, age = manager.get_best_tip()
    
    # Should be peer1 with height 150
    assert height == 150
    assert hash_hex == "11" * 32
    assert peer_id == "peer1" * 8


def test_get_best_tip_with_stale_data():
    """Verify that stale tips are excluded from best tip calculation."""
    registry = PeerRegistry()
    manager = TipManager(registry, freshness_window_s=60.0)
    
    # Register and connect two peers
    peers = []
    for i in range(2):
        session = registry.register(f"tcp://peer{i}:8000", "outbound")
        session_id = session.session_id
        registry.mark_identified(session_id, f"peer{i}" * 8)
        registry.mark_identity_validated(session_id, chain_id=1, genesis_hash="00" * 32)
        peers.append(session_id)
    
    # Update tips - peer0 has higher height but stale data
    now = time.time()
    manager.on_tip_received(peers[0], height=200, hash_hex="00" * 32, tip_time=now - 100.0)
    manager.on_tip_received(peers[1], height=150, hash_hex="11" * 32, tip_time=now)
    
    # Get best tip
    height, hash_hex, peer_id, age = manager.get_best_tip()
    
    # Should be peer1 (fresh) even though height is lower
    assert height == 150
    assert peer_id == "peer1" * 8


def test_tip_stats():
    """Verify that tip statistics are computed correctly."""
    registry = PeerRegistry()
    manager = TipManager(registry, freshness_window_s=60.0)
    
    # Register and connect three peers
    peers = []
    for i in range(3):
        session = registry.register(f"tcp://peer{i}:8000", "outbound")
        session_id = session.session_id
        registry.mark_identified(session_id, f"peer{i}" * 8)
        registry.mark_identity_validated(session_id, chain_id=1, genesis_hash="00" * 32)
        peers.append(session_id)
    
    # Update tips - 2 fresh, 1 stale
    now = time.time()
    manager.on_tip_received(peers[0], height=100, hash_hex="00" * 32, tip_time=now)
    manager.on_tip_received(peers[1], height=150, hash_hex="11" * 32, tip_time=now)
    manager.on_tip_received(peers[2], height=120, hash_hex="22" * 32, tip_time=now - 100.0)
    
    # Get stats
    total, fresh, stale = manager.get_tip_stats()
    
    assert total == 3
    assert fresh == 2
    assert stale == 1


def test_poll_after_handshake_complete():
    """Verify that handshake completion triggers initial tip request."""
    registry = PeerRegistry()
    manager = TipManager(registry)
    
    # Register and connect a peer
    session = registry.register("tcp://peer1:8000", "outbound")
    session_id = session.session_id
    registry.mark_identified(session_id, "peer1" * 8)
    registry.mark_identity_validated(session_id, chain_id=1, genesis_hash="00" * 32)
    
    # Notify handshake complete
    should_request = manager.on_handshake_complete(session_id)
    
    # Should indicate that tip request is needed
    assert should_request is True


def test_cleanup_session():
    """Verify that disconnected sessions are cleaned up."""
    registry = PeerRegistry()
    manager = TipManager(registry)
    
    # Register and connect a peer
    session = registry.register("tcp://peer1:8000", "outbound")
    session_id = session.session_id
    registry.mark_identified(session_id, "peer1" * 8)
    registry.mark_identity_validated(session_id, chain_id=1, genesis_hash="00" * 32)
    
    # Update tip and mark poll
    now = time.time()
    manager.on_tip_received(session_id, height=100, hash_hex="00" * 32, tip_time=now)
    manager.mark_poll_attempted(session_id, now=now)
    
    # Verify poll tracking exists
    assert session_id in manager._last_poll_at
    
    # Cleanup session
    manager.cleanup_session(session_id)
    
    # Verify poll tracking removed
    assert session_id not in manager._last_poll_at


def test_mark_poll_failed():
    """Verify that poll failures are tracked correctly."""
    registry = PeerRegistry()
    manager = TipManager(registry)
    
    # Register and connect a peer
    session = registry.register("tcp://peer1:8000", "outbound")
    session_id = session.session_id
    registry.mark_identified(session_id, "peer1" * 8)
    registry.mark_identity_validated(session_id, chain_id=1, genesis_hash="00" * 32)
    
    # Mark poll failed
    now = time.time()
    manager.mark_poll_failed(session_id, reason="timeout", now=now)
    
    # Verify poll attempt recorded (prevents immediate retry)
    assert session_id in manager._last_poll_at
    assert manager._last_poll_at[session_id] == now


def test_no_poll_without_identity():
    """Verify that peers without validated identity are not polled."""
    registry = PeerRegistry()
    manager = TipManager(registry, poll_interval_s=30.0)
    
    # Register peer and identify but don't validate
    session = registry.register("tcp://peer1:8000", "outbound")
    session_id = session.session_id
    registry.mark_identified(session_id, "peer1" * 8)
    # Note: NOT calling mark_identity_validated
    
    # Check polling
    now = time.time() + 60.0
    to_poll = manager.poll_peer_tips(now=now)
    
    # Should not be polled (identity not validated)
    assert session_id not in to_poll


def test_poll_respects_interval():
    """Verify that poll interval is respected."""
    registry = PeerRegistry()
    manager = TipManager(registry, poll_interval_s=60.0)
    
    # Register and connect a peer
    session = registry.register("tcp://peer1:8000", "outbound")
    session_id = session.session_id
    registry.mark_identified(session_id, "peer1" * 8)
    registry.mark_identity_validated(session_id, chain_id=1, genesis_hash="00" * 32)
    
    # Update tip
    now = time.time()
    manager.on_tip_received(session_id, height=100, hash_hex="00" * 32, tip_time=now)
    
    # Check at 30s - should not poll yet
    to_poll = manager.poll_peer_tips(now=now + 30.0)
    assert session_id not in to_poll
    
    # Check at 65s - should poll now
    to_poll = manager.poll_peer_tips(now=now + 65.0)
    assert session_id in to_poll


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
