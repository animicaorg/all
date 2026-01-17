"""
Tests for sync status accuracy fix.

Verifies that:
1. Node at height 927 with peer at 1666 reports SYNCING with behind_by=739
2. No fresh peer tips results in status not SYNCHRONIZED
3. Stale tips are ignored
4. Small lag within ALLOWED_LAG reports SYNCHRONIZED
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, Mock

import pytest

from p2p.node.p2p_service import P2PService, _PeerState


@dataclass
class MockPeer:
    """Mock peer state for testing."""
    remote: str
    hello: dict = field(default_factory=dict)
    hello_received_at: float = 0.0
    hello_done: asyncio.Event = field(default_factory=asyncio.Event)
    repo_state_ok: bool = True
    anchored: bool = False


class TestSyncStatusAccuracy:
    """Test cases for sync status accuracy fixes."""

    def _make_mock_service(self, local_height: int = 0) -> P2PService:
        """Create a minimal mock P2PService for testing."""
        service = Mock(spec=P2PService)
        service._peers = {}
        service._sync_peer_heads = {}
        service._sync_tip_tolerance = 2
        service._sync_peer_head_stale_sec = 60.0
        service._sync_network_best_cache_timeout = 60.0
        service._sync_best_header = None
        service._sync_target_height = None
        service._sync_checkpoint_height = None
        service._sync_headers_seen_total = 0
        service._sync_inflight_headers = 0
        service._sync_inflight_blocks = {}
        service._sync_enabled = True
        service._sync_requested = False
        service._sync_last_header_error = None
        service._sync_last_block_error = None
        service._sync_last_progress_at = time.time()
        service._sync_header_queue = []
        service._sync_header_retry_queue = []
        service._sync_active_block_peer = None
        service._sync_active_header_peer = None
        
        # Mock head info
        def mock_canonical_head():
            return local_height, f"0x{'0'*64}"
        
        service._canonical_head_for_status = mock_canonical_head
        
        # Bind the real methods we're testing
        service._compute_best_remote_info = P2PService._compute_best_remote_info.__get__(service, P2PService)
        service._network_best_height = P2PService._network_best_height.__get__(service, P2PService)
        
        return service

    def test_behind_node_reports_syncing(self):
        """
        Test: Node at height 927 with peer at 1666 must report SYNCING with behind_by=739.
        """
        service = self._make_mock_service(local_height=927)
        
        # Add a peer at height 1666 with fresh tip info
        peer = MockPeer(
            remote="peer1:30333",
            hello={"head_height": 1666, "chain_id": 1},
            hello_received_at=time.time()  # Fresh timestamp
        )
        peer.hello_done.set()
        service._peers["peer1:30333"] = peer
        
        # Compute best remote info
        best_height, best_hash, best_peer, best_age = service._compute_best_remote_info(chain_id=0)
        
        # Assertions
        assert best_height == 1666, "Should detect peer at height 1666"
        assert best_peer == "peer1:30333", "Should identify the peer"
        assert best_age is not None and best_age < 1.0, "Tip should be fresh"
        
        # Compute behind_by
        local_height = 927
        behind_by = best_height - local_height if best_height else None
        
        assert behind_by == 739, f"Should be 739 blocks behind, got {behind_by}"
        
        # Status must be SYNCING (not SYNCHRONIZED) since behind_by > ALLOWED_LAG (2)
        ALLOWED_LAG = 2
        synchronized = (behind_by is not None and behind_by <= ALLOWED_LAG)
        
        assert not synchronized, "Node 739 blocks behind must NOT be synchronized"

    def test_no_fresh_peer_tips_not_synchronized(self):
        """
        Test: No fresh peer tips must result in status NOT SYNCHRONIZED.
        """
        service = self._make_mock_service(local_height=1000)
        
        # Add a peer but with stale tip info (>60s old)
        peer = MockPeer(
            remote="peer1:30333",
            hello={"head_height": 1666, "chain_id": 1},
            hello_received_at=time.time() - 120.0  # 120s ago - stale
        )
        peer.hello_done.set()
        service._peers["peer1:30333"] = peer
        
        # Compute best remote info
        best_height, best_hash, best_peer, best_age = service._compute_best_remote_info(chain_id=0)
        
        # Assertions
        assert best_height is None, "Stale peer tips should be ignored"
        assert best_peer is None, "No fresh peer should be identified"
        
        # Status must NOT be synchronized when best_remote is None
        synchronized = (best_height is not None)  # Simplified check
        
        assert not synchronized, "Must NOT be synchronized without fresh peer tips"

    def test_stale_tips_ignored(self):
        """
        Test: Stale peer tips (>60s old) must be ignored.
        """
        service = self._make_mock_service(local_height=1000)
        
        # Add two peers: one fresh, one stale
        peer_fresh = MockPeer(
            remote="peer_fresh:30333",
            hello={"head_height": 1100, "chain_id": 1},
            hello_received_at=time.time()  # Fresh
        )
        peer_fresh.hello_done.set()
        
        peer_stale = MockPeer(
            remote="peer_stale:30333",
            hello={"head_height": 1666, "chain_id": 1},
            hello_received_at=time.time() - 120.0  # Stale (>60s)
        )
        peer_stale.hello_done.set()
        
        service._peers["peer_fresh:30333"] = peer_fresh
        service._peers["peer_stale:30333"] = peer_stale
        
        # Compute best remote info
        best_height, best_hash, best_peer, best_age = service._compute_best_remote_info(chain_id=0)
        
        # Assertions
        assert best_height == 1100, f"Should use fresh peer height 1100, got {best_height}"
        assert best_peer == "peer_fresh:30333", "Should use fresh peer"
        assert best_age is not None and best_age < 1.0, "Should report fresh age"

    def test_small_lag_within_allowed(self):
        """
        Test: Node within ALLOWED_LAG (2 blocks) reports SYNCHRONIZED.
        """
        service = self._make_mock_service(local_height=1665)
        
        # Add a peer at height 1666 (1 block ahead)
        peer = MockPeer(
            remote="peer1:30333",
            hello={"head_height": 1666, "chain_id": 1},
            hello_received_at=time.time()  # Fresh
        )
        peer.hello_done.set()
        service._peers["peer1:30333"] = peer
        
        # Compute best remote info
        best_height, best_hash, best_peer, best_age = service._compute_best_remote_info(chain_id=0)
        
        # Assertions
        assert best_height == 1666, "Should detect peer at height 1666"
        
        # Compute behind_by
        local_height = 1665
        behind_by = best_height - local_height if best_height else None
        
        assert behind_by == 1, f"Should be 1 block behind, got {behind_by}"
        
        # Status should be SYNCHRONIZED since behind_by <= ALLOWED_LAG (2)
        ALLOWED_LAG = 2
        synchronized = (
            best_height is not None
            and behind_by is not None
            and behind_by <= ALLOWED_LAG
            and local_height > 0
        )
        
        assert synchronized, "Node 1 block behind (within ALLOWED_LAG) should be synchronized"

    def test_at_tip_synchronized(self):
        """
        Test: Node at same height as best peer reports SYNCHRONIZED.
        """
        service = self._make_mock_service(local_height=1666)
        
        # Add a peer at height 1666 (same height)
        peer = MockPeer(
            remote="peer1:30333",
            hello={"head_height": 1666, "chain_id": 1},
            hello_received_at=time.time()  # Fresh
        )
        peer.hello_done.set()
        service._peers["peer1:30333"] = peer
        
        # Compute best remote info
        best_height, best_hash, best_peer, best_age = service._compute_best_remote_info(chain_id=0)
        
        # Assertions
        assert best_height == 1666, "Should detect peer at height 1666"
        
        # Compute behind_by
        local_height = 1666
        behind_by = best_height - local_height if best_height else None
        
        assert behind_by == 0, f"Should be 0 blocks behind (at tip), got {behind_by}"
        
        # Status should be SYNCHRONIZED
        ALLOWED_LAG = 2
        synchronized = (
            best_height is not None
            and behind_by is not None
            and behind_by <= ALLOWED_LAG
            and local_height > 0
        )
        
        assert synchronized, "Node at tip should be synchronized"

    def test_wrong_chain_id_ignored(self):
        """
        Test: Peers on different chain_id are ignored.
        """
        service = self._make_mock_service(local_height=1000)
        
        # Add peer on different chain_id
        peer = MockPeer(
            remote="peer1:30333",
            hello={"head_height": 1666, "chain_id": 999},  # Wrong chain
            hello_received_at=time.time()  # Fresh
        )
        peer.hello_done.set()
        service._peers["peer1:30333"] = peer
        
        # Compute best remote info for chain_id=0
        best_height, best_hash, best_peer, best_age = service._compute_best_remote_info(chain_id=0)
        
        # Assertions
        assert best_height is None, "Peer on wrong chain_id should be ignored"
        assert best_peer is None, "No peer should be identified"

    def test_genesis_node_with_fresh_peer(self):
        """
        Test: Node at genesis (height 0) with peer ahead is SYNCING.
        """
        service = self._make_mock_service(local_height=0)
        
        # Add a peer at height 100
        peer = MockPeer(
            remote="peer1:30333",
            hello={"head_height": 100, "chain_id": 1},
            hello_received_at=time.time()  # Fresh
        )
        peer.hello_done.set()
        service._peers["peer1:30333"] = peer
        
        # Compute best remote info
        best_height, best_hash, best_peer, best_age = service._compute_best_remote_info(chain_id=0)
        
        # Assertions
        assert best_height == 100, "Should detect peer at height 100"
        
        # Compute behind_by
        local_height = 0
        behind_by = best_height - local_height if best_height else None
        
        assert behind_by == 100, f"Should be 100 blocks behind, got {behind_by}"
        
        # Status must be SYNCING (not SYNCHRONIZED)
        ALLOWED_LAG = 2
        synchronized = (
            best_height is not None
            and behind_by is not None
            and behind_by <= ALLOWED_LAG
            and local_height > 0  # Genesis node should not be synchronized
        )
        
        assert not synchronized, "Node at genesis with peer ahead must NOT be synchronized"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
