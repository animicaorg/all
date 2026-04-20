"""
Test discarding blocks from ineligible peers.

Tests that blocks from peers with handshake_pending status
are properly discarded and re-queued during sync stalls.
"""

import time
from collections import OrderedDict, deque
from unittest.mock import MagicMock, Mock, patch

import pytest


class MockPeerState:
    """Mock peer state for testing."""
    
    def __init__(self, remote: str, hello_done: bool = True, peer_id: str = "peer1"):
        self.remote = remote
        self.peer_id = peer_id
        self.hello_done = Mock()
        self.hello_done.is_set = Mock(return_value=hello_done)
        self.hello = {"head_height": 1000, "chain_id": 1}
        self.ready_for_sync = True
        self.anchored = False
        self.misbehavior_score = 0
        self.direction = "outbound"
        self.netgroup = "group1"
        self.latency_ewma = 100.0
        self.sync_successes = 10
        self.sync_timeouts = 0
        self.sync_failures = 0
        self.not_anchored_count = 0
        self.last_block_request_at = time.time()
        self.broadcast = Mock()
        self.broadcast.last_inventory_at = time.time()
        self.broadcast.last_head_advancement_at = time.time()
        self.broadcast.successful_headers_served = 1
        self.broadcast.successful_blocks_served = 1
        self.broadcast.tip_matches = 1
        self.broadcast.duplicate_header_batches = 0
        self.broadcast.timeouts = 0
        self.broadcast.errors = 0
        self.broadcast.non_broadcasting_since = None
        self.broadcast.last_classification = "good"
        self.last_progress_at = time.time()


class MockSyncBlock:
    """Mock sync block for testing."""
    
    def __init__(self, hash_val: bytes, origin_peer: str):
        self.hash = hash_val
        self.parent_hash = b"\x00" * 32
        self.origin_peer = origin_peer
        self.block = Mock()
        self.received_at = time.time()


class TestDiscardIneligiblePeers:
    """Test discarding blocks from ineligible peers."""
    
    def setup_method(self):
        """Set up test fixtures."""
        # Import the service class
        from p2p.node.p2p_service import P2PService
        
        # Create a minimal mock service
        self.service = Mock(spec=P2PService)
        
        # Set up basic attributes needed by the function
        self.service._peers = {}
        self.service._sync_block_buffer = OrderedDict()
        self.service._sync_inflight_blocks = {}
        self.service._sync_inflight_peers = {}
        self.service._sync_inflight_block_requests = {}
        self.service._sync_block_queue = deque()
        self.service._sync_block_queue_set = set()
        self.service._sync_block_queue_heights = {}
        self.service._sync_cache = Mock()
        self.service._sync_last_block_error_peer = None
        
        # Set up helper methods
        self.service._block_height_hint = Mock(return_value=1000)
        self.service._has_block = Mock(return_value=False)
        self.service._eligible_block_peers = Mock()
        self.service._block_corroboration_status = Mock(
            return_value=(False, "await_header_corroboration", {})
        )
        
        # Bind the actual method from the module
        from p2p.node.p2p_service import P2PService
        self.service._discard_blocks_from_ineligible_peers = (
            P2PService._discard_blocks_from_ineligible_peers.__get__(self.service, P2PService)
        )
    
    def test_discard_buffer_blocks_from_ineligible_peers(self):
        """Test that blocks from ineligible peers in buffer are discarded."""
        # Setup eligible and ineligible peers
        eligible_peer = MockPeerState("eligible.peer:30333", hello_done=True)
        ineligible_peer = MockPeerState("ineligible.peer:30333", hello_done=False)
        
        self.service._peers = {
            ("eligible.peer:30333", "outbound"): eligible_peer,
            ("ineligible.peer:30333", "outbound"): ineligible_peer,
        }
        
        # Mock eligible_block_peers to return only eligible peer
        self.service._eligible_block_peers.return_value = (
            [eligible_peer],
            {"ineligible.peer:30333": "handshake_pending"}
        )
        
        # Add blocks to buffer from both peers
        block_eligible = MockSyncBlock(b"hash_eligible", "eligible.peer:30333")
        block_ineligible = MockSyncBlock(b"hash_ineligible", "ineligible.peer:30333")
        
        self.service._sync_block_buffer = OrderedDict([
            (b"hash_eligible", block_eligible),
            (b"hash_ineligible", block_ineligible),
        ])
        
        # Call the function
        result = self.service._discard_blocks_from_ineligible_peers()
        
        # Verify that ineligible block was discarded
        assert result["buffer"] == 1
        assert b"hash_ineligible" not in self.service._sync_block_buffer
        assert b"hash_eligible" in self.service._sync_block_buffer
        
        # Verify that ineligible block was re-queued
        assert b"hash_ineligible" in self.service._sync_block_queue_set
    
    def test_discard_inflight_blocks_from_ineligible_peers(self):
        """Test that inflight blocks from ineligible peers are discarded."""
        # Setup eligible and ineligible peers
        eligible_peer = MockPeerState("eligible.peer:30333", hello_done=True)
        ineligible_peer = MockPeerState("ineligible.peer:30333", hello_done=False)
        
        self.service._peers = {
            ("eligible.peer:30333", "outbound"): eligible_peer,
            ("ineligible.peer:30333", "outbound"): ineligible_peer,
        }
        
        # Mock eligible_block_peers to return only eligible peer
        self.service._eligible_block_peers.return_value = (
            [eligible_peer],
            {"ineligible.peer:30333": "handshake_pending"}
        )
        
        # Add inflight blocks from both peers
        now = time.time()
        self.service._sync_inflight_blocks = {
            b"hash_eligible": now,
            b"hash_ineligible": now,
        }
        self.service._sync_inflight_peers = {
            b"hash_eligible": "eligible.peer:30333",
            b"hash_ineligible": "ineligible.peer:30333",
        }
        
        # Call the function
        result = self.service._discard_blocks_from_ineligible_peers()
        
        # Verify that ineligible block was discarded
        assert result["inflight"] == 1
        assert b"hash_ineligible" not in self.service._sync_inflight_blocks
        assert b"hash_ineligible" not in self.service._sync_inflight_peers
        assert b"hash_eligible" in self.service._sync_inflight_blocks
        
        # Verify that ineligible block was re-queued
        assert b"hash_ineligible" in self.service._sync_block_queue_set
    
    def test_no_discard_when_all_peers_eligible(self):
        """Test that no blocks are discarded when all peers are eligible."""
        # Setup all eligible peers
        peer1 = MockPeerState("peer1:30333", hello_done=True)
        peer2 = MockPeerState("peer2:30333", hello_done=True)
        
        self.service._peers = {
            ("peer1:30333", "outbound"): peer1,
            ("peer2:30333", "outbound"): peer2,
        }
        
        # Mock eligible_block_peers to return all peers
        self.service._eligible_block_peers.return_value = (
            [peer1, peer2],
            {}
        )
        
        # Add blocks from eligible peers
        block1 = MockSyncBlock(b"hash1", "peer1:30333")
        block2 = MockSyncBlock(b"hash2", "peer2:30333")
        
        self.service._sync_block_buffer = OrderedDict([
            (b"hash1", block1),
            (b"hash2", block2),
        ])
        
        # Call the function
        result = self.service._discard_blocks_from_ineligible_peers()
        
        # Verify that no blocks were discarded
        assert result["buffer"] == 0
        assert result["inflight"] == 0
        assert len(self.service._sync_block_buffer) == 2

    def test_keep_buffer_block_if_corroborated(self):
        """Corroborated blocks should remain buffered even if origin peer is ineligible."""
        eligible_peer = MockPeerState("eligible.peer:30333", hello_done=True)
        ineligible_peer = MockPeerState("ineligible.peer:30333", hello_done=False)

        self.service._peers = {
            ("eligible.peer:30333", "outbound"): eligible_peer,
            ("ineligible.peer:30333", "outbound"): ineligible_peer,
        }
        self.service._eligible_block_peers.return_value = (
            [eligible_peer],
            {"ineligible.peer:30333": "handshake_pending"},
        )
        self.service._block_corroboration_status.return_value = (
            True,
            "quorum_met",
            {"votes": 2, "required": 2},
        )

        block_ineligible = MockSyncBlock(b"hash_ineligible", "ineligible.peer:30333")
        self.service._sync_block_buffer = OrderedDict(
            [(b"hash_ineligible", block_ineligible)]
        )

        result = self.service._discard_blocks_from_ineligible_peers()

        assert result["buffer"] == 0
        assert b"hash_ineligible" in self.service._sync_block_buffer
    
    def test_clear_cache_when_error_peer_ineligible(self):
        """Test that cache is cleared when error peer becomes ineligible."""
        # Setup peers
        eligible_peer = MockPeerState("eligible.peer:30333", hello_done=True)
        ineligible_peer = MockPeerState("ineligible.peer:30333", hello_done=False)
        
        self.service._peers = {
            ("eligible.peer:30333", "outbound"): eligible_peer,
            ("ineligible.peer:30333", "outbound"): ineligible_peer,
        }
        
        # Mock eligible_block_peers to return only eligible peer
        self.service._eligible_block_peers.return_value = (
            [eligible_peer],
            {"ineligible.peer:30333": "handshake_pending"}
        )
        
        # Set error peer to ineligible peer
        self.service._sync_last_block_error_peer = "ineligible.peer:30333"
        
        # Call the function
        result = self.service._discard_blocks_from_ineligible_peers()
        
        # Verify that cache was cleared
        assert result["cache"] == 1
        self.service._sync_cache.clear.assert_called_once()


class TestForcePeerPrioritization:
    """Test that force peers are prioritized in peer selection."""
    
    def test_force_peers_prioritized_in_sync_peer_selection(self):
        """Test that force peers are selected first."""
        from p2p.node.p2p_service import FORCE_SYNC_HEADER_PEERS
        
        # Ensure the force peer constant is set
        assert "144.126.133.21:30333" in FORCE_SYNC_HEADER_PEERS
    
    def test_force_peer_bypasses_eligibility_checks(self):
        """Test that force peers bypass eligibility checks."""
        from p2p.node.p2p_service import P2PService, FORCE_SYNC_HEADER_PEERS
        
        # Create a mock service
        service = Mock(spec=P2PService)
        service._is_self_address = Mock(return_value=False)
        service._is_peer_exempt = Mock(return_value=False)
        service._is_banned = Mock(return_value=False)
        service._genesis_header_hash = Mock(return_value=b"genesis")
        service._genesis_block_hash = Mock(return_value=b"genesis")
        service._genesis_identity = Mock(return_value=b"identity")
        service._network_params_hash = Mock(return_value=b"params")
        service._fork_id = Mock(return_value=1)
        service._consensus_id = Mock(return_value="consensus")
        service._protocol_version = Mock(return_value="1")
        service._peer_head_matches_known_chain = Mock(return_value=True)
        service._sync_peer_backoff = {}
        service._sync_peer_backoff_reason = {}
        service.chain_id = 1
        
        # Bind the method
        service._sync_peer_eligibility = (
            P2PService._sync_peer_eligibility.__get__(service, P2PService)
        )
        
        # Create a force peer
        force_peer_remote = list(FORCE_SYNC_HEADER_PEERS)[0]
        force_peer = MockPeerState(force_peer_remote, hello_done=False)
        
        # Call eligibility check - should return True even with hello_done=False
        eligible, reason = service._sync_peer_eligibility(force_peer)
        
        # Verify force peer is eligible
        assert eligible is True
        assert reason == "force_eligible"


class TestBlockCorroboration:
    """Test block corroboration logic."""

    def setup_method(self):
        from p2p.node.p2p_service import P2PService

        self.service = Mock(spec=P2PService)
        self.service._sync_block_confirm_min_peers = 2
        self.service._sync_block_confirm_require_force_peer = True
        self.service._header_vote_peers = Mock(return_value=set())
        self.service._eligible_block_peers = Mock(return_value=([], {}))
        self.service._is_force_sync_remote = Mock(
            side_effect=lambda remote: str(remote).startswith("144.126.133.21")
        )

        self.service._block_corroboration_status = (
            P2PService._block_corroboration_status.__get__(self.service, P2PService)
        )

    def test_quorum_accepts_block(self):
        self.service._header_vote_peers.return_value = {
            "peer1:30333",
            "peer2:30333",
        }
        self.service._eligible_block_peers.return_value = ([], {})

        ok, reason, ctx = self.service._block_corroboration_status(
            b"hash", origin_remote="peer1:30333"
        )

        assert ok is True
        assert reason == "quorum_met"
        assert ctx["votes"] == 2

    def test_low_peer_mode_requires_force_peer_vote(self):
        force_peer = MockPeerState("144.126.133.21:30333", hello_done=True)
        self.service._eligible_block_peers.return_value = ([force_peer], {})
        self.service._header_vote_peers.return_value = {"peer1:30333"}

        ok, reason, _ctx = self.service._block_corroboration_status(
            b"hash", origin_remote="peer1:30333"
        )

        assert ok is False
        assert reason == "await_force_peer_header"

    def test_low_peer_mode_accepts_force_peer_vote(self):
        force_peer = MockPeerState("144.126.133.21:30333", hello_done=True)
        self.service._eligible_block_peers.return_value = ([force_peer], {})
        self.service._header_vote_peers.return_value = {"144.126.133.21:30333"}

        ok, reason, _ctx = self.service._block_corroboration_status(
            b"hash", origin_remote="peer1:30333"
        )

        assert ok is True
        assert reason == "force_peer_confirmed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
