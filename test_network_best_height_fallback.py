"""
Test to verify that _network_best_height() includes peer's advertised head_height
even when _sync_peer_heads is stale or in cooldown.

This test reproduces the bug where nodes miss the highest network height
because they only check _sync_peer_heads and ignore peer.hello["head_height"].
"""
import time
from unittest.mock import Mock, MagicMock
from dataclasses import dataclass
from typing import Optional

from p2p.node.p2p_service import P2PService


@dataclass
class _PeerHeadInfo:
    """Mock peer head info structure"""
    height: int
    updated_at: float
    source: str
    cooldown_until: Optional[float] = None
    last_error: Optional[str] = None


def test_network_best_height_with_stale_peer_heads():
    """
    Test that _network_best_height() returns the peer's advertised head_height
    even when _sync_peer_heads data is stale.
    
    Scenario:
    - Peer has head_height=1000 in hello message (fresh)
    - _sync_peer_heads[peer] = 995, but is stale (updated 120 seconds ago)
    - Expected: network_best_height should return 1000 (from hello), not 995
    
    Without the fix: Returns None (ignores both stale _sync_peer_heads AND hello)
    With the fix: Returns 1000 (uses hello["head_height"] as fallback)
    """
    # Create mock peer
    peer = MagicMock()
    peer.hello_done.is_set.return_value = True
    peer.repo_state_ok = True
    peer.hello = {"head_height": 1000}
    peer.hello_received_at = time.time()  # Fresh hello
    peer.remote = "peer1"
    
    # Create mock P2P service with the peer
    service = MagicMock()
    service._peers = {"peer1": peer}
    service._sync_peer_head_stale_sec = 60.0  # 60 second threshold
    service._sync_network_best_cache_timeout = 300.0
    
    # Stale _sync_peer_heads entry (updated 120 seconds ago)
    service._sync_peer_heads = {
        "peer1": _PeerHeadInfo(
            height=995,
            updated_at=time.time() - 120.0,  # Stale!
            source="test",
        )
    }
    
    # Call the actual _network_best_height method
    result = P2PService._network_best_height(service)
    
    # With the fix: should return 1000 (from peer.hello["head_height"])
    assert result == 1000, f"Expected 1000 (peer's advertised height), got {result}"
    print("✓ Test passed: _network_best_height correctly uses peer.hello['head_height'] when _sync_peer_heads is stale")


def test_network_best_height_with_cooldown():
    """
    Test that _network_best_height() returns the peer's advertised head_height
    even when _sync_peer_heads is in cooldown.
    
    Scenario:
    - Peer has head_height=2000 in hello message
    - _sync_peer_heads[peer] = 1995, but is in cooldown (cooldown_until = now + 30s)
    - Expected: network_best_height should return 2000 (from hello)
    
    Without the fix: Returns None (ignores both cooldown _sync_peer_heads AND hello)
    With the fix: Returns 2000 (uses hello["head_height"] as fallback)
    """
    # Create mock peer
    peer = MagicMock()
    peer.hello_done.is_set.return_value = True
    peer.repo_state_ok = True
    peer.hello = {"head_height": 2000}
    peer.hello_received_at = time.time()
    peer.remote = "peer1"
    
    # Create mock P2P service
    service = MagicMock()
    service._peers = {"peer1": peer}
    service._sync_peer_head_stale_sec = 60.0
    service._sync_network_best_cache_timeout = 300.0
    
    # Fresh _sync_peer_heads entry but in cooldown
    now = time.time()
    service._sync_peer_heads = {
        "peer1": _PeerHeadInfo(
            height=1995,
            updated_at=now,  # Fresh
            source="test",
            cooldown_until=now + 30.0,  # In cooldown!
        )
    }
    
    # Call the actual _network_best_height method
    result = P2PService._network_best_height(service)
    
    # With the fix: should return 2000 (from peer.hello["head_height"])
    assert result == 2000, f"Expected 2000 (peer's advertised height), got {result}"
    print("✓ Test passed: _network_best_height correctly uses peer.hello['head_height'] when _sync_peer_heads is in cooldown")


def test_network_best_height_with_missing_peer_heads():
    """
    Test that _network_best_height() returns the peer's advertised head_height
    even when _sync_peer_heads has no entry for the peer.
    
    Scenario:
    - Peer has head_height=3000 in hello message
    - _sync_peer_heads has no entry for this peer
    - Expected: network_best_height should return 3000 (from hello)
    
    Without the fix: Returns None (no _sync_peer_heads entry, ignores hello)
    With the fix: Returns 3000 (uses hello["head_height"] as fallback)
    """
    # Create mock peer
    peer = MagicMock()
    peer.hello_done.is_set.return_value = True
    peer.repo_state_ok = True
    peer.hello = {"head_height": 3000}
    peer.hello_received_at = time.time()
    peer.remote = "peer1"
    
    # Create mock P2P service
    service = MagicMock()
    service._peers = {"peer1": peer}
    service._sync_peer_head_stale_sec = 60.0
    service._sync_network_best_cache_timeout = 300.0
    service._sync_peer_heads = {}  # No entry for peer1!
    
    # Call the actual _network_best_height method
    result = P2PService._network_best_height(service)
    
    # With the fix: should return 3000 (from peer.hello["head_height"])
    assert result == 3000, f"Expected 3000 (peer's advertised height), got {result}"
    print("✓ Test passed: _network_best_height correctly uses peer.hello['head_height'] when _sync_peer_heads is missing")


def test_network_best_height_uses_max_of_all_sources():
    """
    Test that _network_best_height() correctly returns the maximum height
    across all sources: _sync_peer_heads, hello["head_height"], and hello["network_best_height"].
    
    Scenario:
    - Peer1: _sync_peer_heads=1000, head_height=1100, network_best_height=900
    - Peer2: _sync_peer_heads=950, head_height=1050, network_best_height=1200
    - Expected: max(1000, 1100, 900, 950, 1050, 1200) = 1200
    """
    now = time.time()
    
    # Create two mock peers
    peer1 = MagicMock()
    peer1.hello_done.is_set.return_value = True
    peer1.repo_state_ok = True
    peer1.hello = {"head_height": 1100, "network_best_height": 900}
    peer1.hello_received_at = now
    peer1.remote = "peer1"
    
    peer2 = MagicMock()
    peer2.hello_done.is_set.return_value = True
    peer2.repo_state_ok = True
    peer2.hello = {"head_height": 1050, "network_best_height": 1200}
    peer2.hello_received_at = now
    peer2.remote = "peer2"
    
    # Create mock P2P service
    service = MagicMock()
    service._peers = {"peer1": peer1, "peer2": peer2}
    service._sync_peer_head_stale_sec = 60.0
    service._sync_network_best_cache_timeout = 300.0
    
    # Fresh _sync_peer_heads entries
    service._sync_peer_heads = {
        "peer1": _PeerHeadInfo(height=1000, updated_at=now, source="test"),
        "peer2": _PeerHeadInfo(height=950, updated_at=now, source="test"),
    }
    
    # Call the actual _network_best_height method
    result = P2PService._network_best_height(service)
    
    # Should return 1200 (the maximum across all sources)
    assert result == 1200, f"Expected 1200 (max of all sources), got {result}"
    print("✓ Test passed: _network_best_height correctly returns maximum across all sources")


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("Testing _network_best_height() fallback to peer.hello['head_height']")
    print("=" * 80 + "\n")
    
    try:
        test_network_best_height_with_stale_peer_heads()
        test_network_best_height_with_cooldown()
        test_network_best_height_with_missing_peer_heads()
        test_network_best_height_uses_max_of_all_sources()
        
        print("\n" + "=" * 80)
        print("✅ All tests passed! The fix correctly includes peer.hello['head_height']")
        print("=" * 80)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        raise
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        raise
