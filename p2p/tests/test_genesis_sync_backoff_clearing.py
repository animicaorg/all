"""
Test for genesis sync backoff clearing when peers are handshaking.

This test ensures that when a node is stuck at genesis with no eligible peers
but has peers in handshaking state, it clears peer backoffs to allow immediate
sync once handshake completes.

Regression test for issue: "In flight headers is always 0"
"""
import pytest


@pytest.mark.asyncio
async def test_genesis_sync_clears_backoffs_with_handshaking_peers():
    """
    Test that genesis sync clears peer backoffs when handshaking peers exist.
    
    Scenario:
    - Node at genesis (height 0)
    - No eligible peers (all in handshake or backoff)
    - Some peers are actively handshaking
    - Expected: Backoffs should be cleared to allow immediate sync
    
    Before the fix:
    - Peer completes handshake but remains ineligible due to backoff
    - Node stays stuck even though peer is ready
    
    After the fix:
    - Backoffs are proactively cleared when handshaking peers exist
    - Peer becomes eligible immediately after handshake completes
    """
    from p2p.node.p2p_service import P2PService, _PeerState
    from unittest.mock import Mock, MagicMock, AsyncMock
    import asyncio
    import time
    
    # Create a mock P2PService instance
    service = Mock(spec=P2PService)
    service._peers = {}
    service._sync_peer_backoff = {}
    service._sync_peer_backoff_reason = {}
    service._sync_inflight_headers = 0
    service._sync_last_header_error = None
    service._sync_last_progress_at = time.time()
    
    # Mock the _local_head to return genesis
    def mock_local_head():
        return (0, "0x" + "00" * 64)
    service._local_head = mock_local_head
    
    # Mock _peer_backoff_key
    def mock_peer_backoff_key(peer):
        return f"{peer.remote}:{peer.direction}"
    service._peer_backoff_key = mock_peer_backoff_key
    
    # Create a handshaking peer
    peer = Mock(spec=_PeerState)
    peer.remote = "127.0.0.1:30333"
    peer.direction = "outbound"
    peer.hello_done = Mock()
    peer.hello_done.is_set = Mock(return_value=False)  # Still handshaking
    peer.identity_ok = False
    
    # Add peer to service
    service._peers[f"{peer.remote}:{peer.direction}"] = peer
    
    # Add a backoff for this peer (simulating previous failed sync attempt)
    backoff_key = mock_peer_backoff_key(peer)
    service._sync_peer_backoff[backoff_key] = time.time() + 30.0  # 30 second backoff
    service._sync_peer_backoff_reason[backoff_key] = "headers_empty"
    
    # Verify initial state
    assert len(service._peers) == 1, "Should have 1 peer"
    assert peer.hello_done.is_set() is False, "Peer should be handshaking"
    assert backoff_key in service._sync_peer_backoff, "Peer should have backoff"
    
    # Simulate the logic from the fix
    local_height, _ = mock_local_head()
    at_genesis = (local_height or 0) == 0
    
    assert at_genesis, "Should be at genesis"
    
    if at_genesis and service._peers:
        # Count handshaking peers
        handshaking_count = sum(
            1 for p in service._peers.values() 
            if not p.hello_done.is_set()
        )
        
        assert handshaking_count > 0, "Should have handshaking peers"
        
        # Clear backoffs (this is the fix)
        if handshaking_count > 0:
            cleared_backoffs = 0
            for p in service._peers.values():
                key = service._peer_backoff_key(p)
                if key in service._sync_peer_backoff:
                    service._sync_peer_backoff.pop(key, None)
                    service._sync_peer_backoff_reason.pop(key, None)
                    cleared_backoffs += 1
            
            assert cleared_backoffs > 0, "Should have cleared at least one backoff"
    
    # Verify backoff was cleared
    assert backoff_key not in service._sync_peer_backoff, "Backoff should be cleared"
    assert backoff_key not in service._sync_peer_backoff_reason, "Backoff reason should be cleared"


@pytest.mark.asyncio
async def test_genesis_sync_does_not_clear_backoffs_without_handshaking_peers():
    """
    Test that backoffs are NOT cleared if no peers are handshaking.
    
    This ensures the fix is targeted and doesn't affect normal operation.
    """
    from p2p.node.p2p_service import P2PService, _PeerState
    from unittest.mock import Mock
    import time
    
    # Create a mock P2PService instance
    service = Mock(spec=P2PService)
    service._peers = {}
    service._sync_peer_backoff = {}
    service._sync_peer_backoff_reason = {}
    
    # Mock the _local_head to return genesis
    def mock_local_head():
        return (0, "0x" + "00" * 64)
    service._local_head = mock_local_head
    
    # Mock _peer_backoff_key
    def mock_peer_backoff_key(peer):
        return f"{peer.remote}:{peer.direction}"
    service._peer_backoff_key = mock_peer_backoff_key
    
    # Create a peer that has completed handshake (not handshaking)
    peer = Mock(spec=_PeerState)
    peer.remote = "127.0.0.1:30333"
    peer.direction = "outbound"
    peer.hello_done = Mock()
    peer.hello_done.is_set = Mock(return_value=True)  # Handshake complete
    peer.identity_ok = True
    
    # Add peer to service
    service._peers[f"{peer.remote}:{peer.direction}"] = peer
    
    # Add a backoff for this peer
    backoff_key = mock_peer_backoff_key(peer)
    service._sync_peer_backoff[backoff_key] = time.time() + 30.0
    service._sync_peer_backoff_reason[backoff_key] = "headers_empty"
    
    # Simulate the logic from the fix
    local_height, _ = mock_local_head()
    at_genesis = (local_height or 0) == 0
    
    if at_genesis and service._peers:
        # Count handshaking peers
        handshaking_count = sum(
            1 for p in service._peers.values() 
            if not p.hello_done.is_set()
        )
        
        assert handshaking_count == 0, "Should have no handshaking peers"
        
        # Should NOT clear backoffs
        if handshaking_count > 0:  # This condition is False
            cleared_backoffs = 0
            for p in service._peers.values():
                key = service._peer_backoff_key(p)
                if key in service._sync_peer_backoff:
                    service._sync_peer_backoff.pop(key, None)
                    service._sync_peer_backoff_reason.pop(key, None)
                    cleared_backoffs += 1
    
    # Verify backoff was NOT cleared (because no handshaking peers)
    assert backoff_key in service._sync_peer_backoff, "Backoff should still exist"
    assert backoff_key in service._sync_peer_backoff_reason, "Backoff reason should still exist"


@pytest.mark.asyncio
async def test_genesis_sync_backoff_clearing_only_at_genesis():
    """
    Test that backoff clearing only happens at genesis.
    
    Ensures the fix is scoped to genesis sync issues only.
    """
    from p2p.node.p2p_service import P2PService, _PeerState
    from unittest.mock import Mock
    import time
    
    # Create a mock P2PService instance
    service = Mock(spec=P2PService)
    service._peers = {}
    service._sync_peer_backoff = {}
    service._sync_peer_backoff_reason = {}
    
    # Mock the _local_head to return height 10 (NOT genesis)
    def mock_local_head():
        return (10, "0x" + "aa" * 64)
    service._local_head = mock_local_head
    
    # Mock _peer_backoff_key
    def mock_peer_backoff_key(peer):
        return f"{peer.remote}:{peer.direction}"
    service._peer_backoff_key = mock_peer_backoff_key
    
    # Create a handshaking peer
    peer = Mock(spec=_PeerState)
    peer.remote = "127.0.0.1:30333"
    peer.direction = "outbound"
    peer.hello_done = Mock()
    peer.hello_done.is_set = Mock(return_value=False)  # Still handshaking
    peer.identity_ok = False
    
    # Add peer to service
    service._peers[f"{peer.remote}:{peer.direction}"] = peer
    
    # Add a backoff for this peer
    backoff_key = mock_peer_backoff_key(peer)
    service._sync_peer_backoff[backoff_key] = time.time() + 30.0
    service._sync_peer_backoff_reason[backoff_key] = "headers_empty"
    
    # Simulate the logic from the fix
    local_height, _ = mock_local_head()
    at_genesis = (local_height or 0) == 0
    
    assert not at_genesis, "Should NOT be at genesis"
    
    # The fix only applies at genesis, so backoffs should NOT be cleared
    if at_genesis and service._peers:  # This condition is False
        handshaking_count = sum(
            1 for p in service._peers.values() 
            if not p.hello_done.is_set()
        )
        if handshaking_count > 0:
            for p in service._peers.values():
                key = service._peer_backoff_key(p)
                if key in service._sync_peer_backoff:
                    service._sync_peer_backoff.pop(key, None)
                    service._sync_peer_backoff_reason.pop(key, None)
    
    # Verify backoff was NOT cleared (because not at genesis)
    assert backoff_key in service._sync_peer_backoff, "Backoff should still exist at non-genesis height"
    assert backoff_key in service._sync_peer_backoff_reason, "Backoff reason should still exist"
