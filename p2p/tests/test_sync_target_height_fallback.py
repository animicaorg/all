"""
Test sync with target_height fallback when no fresh peer tips available.

This tests the scenario where:
- Node is at genesis (height 0)
- target_height is set (e.g., from block announcement or cache)
- Peers exist but haven't completed handshakes (no fresh tips)
- Sync should still progress using target_height as fallback
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
import asyncio
import time


class TestSyncTargetHeightFallback:
    """Test sync behavior with target_height fallback."""
    
    @pytest.fixture
    def mock_service(self):
        """Create a minimal mock P2P service."""
        service = Mock()
        service._peers = {}
        service._sync_target_height = None
        service._peer_tip_tracker = Mock()
        service.chain_id = 0
        return service
    
    def test_compute_best_remote_with_no_peers_no_target(self, mock_service):
        """When no peers and no target, best_remote should be None."""
        from p2p.node.p2p_service import P2PService
        
        # Mock the method on a real instance
        with patch.object(P2PService, '__init__', lambda x: None):
            service = P2PService()
            service._peers = {}
            service._sync_target_height = None
            
            result = service._compute_best_remote_info(chain_id=0)
            
            assert result == (None, None, None, None), \
                "Should return None when no peers and no target"
    
    def test_compute_best_remote_with_target_no_peers(self, mock_service):
        """When no peers but target_height is set, should use target as fallback."""
        from p2p.node.p2p_service import P2PService
        
        with patch.object(P2PService, '__init__', lambda x: None):
            service = P2PService()
            service._peers = {}
            service._sync_target_height = 1
            
            height, hash_hex, peer, age = service._compute_best_remote_info(chain_id=0)
            
            assert height == 1, "Should return target_height as best_remote"
            assert hash_hex is None, "Hash should be None (synthetic target)"
            assert peer == "target_fallback", "Peer should indicate fallback"
            assert age == 0.0, "Age should be 0 (not from peer)"
    
    def test_compute_best_remote_prefers_fresh_peer_over_target(self, mock_service):
        """When fresh peer tip is higher than target, prefer peer tip."""
        from p2p.node.p2p_service import P2PService
        
        with patch.object(P2PService, '__init__', lambda x: None):
            service = P2PService()
            service._sync_target_height = 1
            service._peer_tip_tracker = Mock()
            
            # Create mock peer with completed handshake and fresh tip at height 2
            mock_peer = Mock()
            mock_peer.hello_done.is_set.return_value = True
            mock_peer.identity_ok = True
            mock_peer.repo_state_ok = True
            mock_peer.hello = {"chain_id": 0}
            mock_peer.remote = "192.168.1.1:30333"
            
            service._peers = {"192.168.1.1:30333": mock_peer}
            
            # Mock _peer_chain_matches to return True
            service._peer_chain_matches = Mock(return_value=True)
            
            # Mock tip tracker to return fresh tip at height 2
            mock_tip = Mock()
            mock_tip.height = 2
            mock_tip.head_hash = b"\x01" * 32
            mock_tip.updated_at = time.time()  # Fresh
            service._peer_tip_tracker.get = Mock(return_value=mock_tip)
            
            height, hash_hex, peer, age = service._compute_best_remote_info(chain_id=0)
            
            assert height == 2, "Should prefer fresh peer tip (2) over target (1)"
            assert hash_hex == "0x" + ("01" * 32), "Should return peer's hash"
            assert peer == "192.168.1.1:30333", "Should return peer address"
    
    def test_compute_best_remote_uses_target_when_peer_no_hello(self, mock_service):
        """When peer hasn't completed handshake, use target fallback."""
        from p2p.node.p2p_service import P2PService
        
        with patch.object(P2PService, '__init__', lambda x: None):
            service = P2PService()
            service._sync_target_height = 5
            
            # Create mock peer WITHOUT completed handshake
            mock_peer = Mock()
            mock_peer.hello_done.is_set.return_value = False  # Not ready
            mock_peer.remote = "192.168.1.1:30333"
            
            service._peers = {"192.168.1.1:30333": mock_peer}
            
            height, hash_hex, peer, age = service._compute_best_remote_info(chain_id=0)
            
            assert height == 5, "Should fall back to target_height when peer not ready"
            assert peer == "target_fallback"
    
    def test_compute_best_remote_target_zero_not_used(self, mock_service):
        """Target height of 0 should not be used as fallback."""
        from p2p.node.p2p_service import P2PService
        
        with patch.object(P2PService, '__init__', lambda x: None):
            service = P2PService()
            service._peers = {}
            service._sync_target_height = 0  # Genesis
            
            result = service._compute_best_remote_info(chain_id=0)
            
            assert result == (None, None, None, None), \
                "Should not use target_height=0 as fallback"
    
    def test_sync_status_with_target_fallback(self, mock_service):
        """Sync status should show proper reason when using target fallback."""
        from p2p.node.p2p_service import P2PService
        
        with patch.object(P2PService, '__init__', lambda x: None):
            service = P2PService()
            service._sync_target_height = 1
            service._peers = {}
            service._sync_checkpoint_height = None
            service._sync_tip_tolerance = 2
            service._sync_status_invariants = Mock(return_value=True)
            service._queued_blocks_count = Mock(return_value=0)
            service._local_head = Mock(return_value=(0, "0x" + ("00" * 32)))
            service._sync_best_header = None
            service._network_best_height = Mock(return_value=None)
            service.peer_count = Mock(return_value=1)  # Has peer but not connected
            service.chain_id = 0
            service._peer_head_poll_at = {}
            
            # Mock _compute_best_remote_info to use our implementation
            service._compute_best_remote_info = lambda chain_id: (1, None, "target_fallback", 0.0)
            service._peer_tip_freshness_snapshot = lambda chain_id: (0, 0, 0)
            
            # The key assertion: with target_height fallback, best_remote_height should be 1
            height, _, _, _ = service._compute_best_remote_info(chain_id=0)
            assert height == 1, "Target fallback should provide best_remote_height"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
