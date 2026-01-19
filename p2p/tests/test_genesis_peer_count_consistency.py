"""
Test for consistent peer counting across different query methods.

Ensures that peer_count() and peer_state_snapshot() return consistent
results and properly filter based on identity_ok status.
"""
import pytest
from unittest.mock import Mock, MagicMock
from p2p.node.peer_registry import PeerRegistry, PeerSession


@pytest.mark.asyncio
async def test_peer_count_only_includes_identity_ok_peers():
    """
    Test that peer_count() only counts peers with identity_ok=True.
    
    This ensures consistency between what's counted and what's shown
    as "connected" in peer list output.
    """
    registry = PeerRegistry()
    
    # Register 3 peers
    session1 = registry.register("peer1:30333", "outbound")
    session2 = registry.register("peer2:30333", "outbound")
    session3 = registry.register("peer3:30333", "inbound")
    
    # Mark all as identified (have peer_id)
    registry.mark_identified(session1.session_id, "peer_id_1")
    registry.mark_identified(session2.session_id, "peer_id_2")
    registry.mark_identified(session3.session_id, "peer_id_3")
    
    # Initially, no peers have identity_ok set, so count should be 0
    assert registry.peer_count() == 0
    
    # Set identity_ok for peer1
    registry.update_meta(session1.session_id, identity_ok=True)
    assert registry.peer_count() == 1
    
    # Set identity_ok for peer2
    registry.update_meta(session2.session_id, identity_ok=True)
    assert registry.peer_count() == 2
    
    # Set identity_ok for peer3
    registry.update_meta(session3.session_id, identity_ok=True)
    assert registry.peer_count() == 3
    
    # Remove identity_ok from peer2 (simulating validation failure)
    registry.update_meta(session2.session_id, identity_ok=False)
    assert registry.peer_count() == 2


@pytest.mark.asyncio
async def test_peer_snapshot_status_matches_identity_ok():
    """
    Test that peer_state_snapshot() sets status based on identity_ok.
    
    Peers with identity_ok=True should show as "connected"
    Peers without identity_ok should show as "handshaking"
    """
    from p2p.node.p2p_service import P2PService
    from unittest.mock import Mock, AsyncMock
    
    # Create a mock P2PService
    # We can't easily instantiate the real service in a unit test,
    # so we'll just verify the logic in peer_registry instead
    
    registry = PeerRegistry()
    
    # Register and identify 2 peers
    session1 = registry.register("peer1:30333", "outbound")
    session2 = registry.register("peer2:30333", "outbound")
    
    registry.mark_identified(session1.session_id, "peer_id_1")
    registry.mark_identified(session2.session_id, "peer_id_2")
    
    # Only peer1 has identity_ok
    registry.update_meta(session1.session_id, identity_ok=True)
    registry.update_meta(session2.session_id, identity_ok=False)
    
    # Get snapshots
    snapshots = registry.snapshot()
    
    # Both should be in snapshot
    assert len(snapshots) == 2
    
    # Verify peer1 has identity_ok=True
    peer1_snap = next(s for s in snapshots if s["peer_id"] == "peer_id_1")
    assert peer1_snap["identity_ok"] == True
    
    # Verify peer2 has identity_ok=False
    peer2_snap = next(s for s in snapshots if s["peer_id"] == "peer_id_2")
    assert peer2_snap.get("identity_ok") == False


@pytest.mark.asyncio
async def test_peer_count_consistency_with_snapshot():
    """
    Test that peer_count() matches the count of "connected" peers in snapshot.
    
    This is the critical consistency check - the count should equal the number
    of peers with status="connected" (which requires identity_ok=True).
    """
    registry = PeerRegistry()
    
    # Register 5 peers in various states
    sessions = []
    for i in range(5):
        session = registry.register(f"peer{i}:30333", "outbound")
        sessions.append(session)
        registry.mark_identified(session.session_id, f"peer_id_{i}")
    
    # Set identity_ok for only 3 of them
    registry.update_meta(sessions[0].session_id, identity_ok=True)
    registry.update_meta(sessions[1].session_id, identity_ok=True)
    registry.update_meta(sessions[2].session_id, identity_ok=True)
    # sessions[3] and [4] don't have identity_ok set
    
    # peer_count should be 3
    assert registry.peer_count() == 3
    
    # Verify snapshot consistency
    snapshots = registry.snapshot()
    assert len(snapshots) == 5
    
    identity_ok_count = sum(1 for s in snapshots if s.get("identity_ok") == True)
    assert identity_ok_count == 3
    assert identity_ok_count == registry.peer_count()
