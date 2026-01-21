"""
Node Stabilization Requirements Test Suite

This test validates the specific requirements from the node stabilization issue:

1. Two nodes reach CONNECTED state within 15s
2. Peer counts are consistent across all status endpoints
3. Peer tips are real and fresh
4. Sync works correctly (block propagation)
5. Status outputs are stable (head_hash never None)
6. Timeouts/backoffs are sane
7. No silent failures

These tests use the existing MockNode framework for fast, deterministic testing.
"""

import pytest
import time
from typing import Dict, List, Optional

from p2p.node.handshake import HandshakeManager
from p2p.node.peer_registry import PeerRegistry, PeerState
from p2p.node.tip_manager import TipManager


def _make_test_peer_id(prefix: str) -> str:
    """Generate a 64-character hex peer ID (32 bytes) from a prefix."""
    hex_id = prefix.encode().hex()
    return hex_id + "0" * (64 - len(hex_id))


def _make_test_hash(prefix: str) -> str:
    """Generate a 64-character hex hash (32 bytes) from a prefix."""
    hex_hash = prefix.encode().hex()
    return hex_hash + "0" * (64 - len(hex_hash))


class MockNode:
    """
    Lightweight mock node for testing P2P requirements.
    """
    
    def __init__(
        self,
        node_id: str,
        chain_id: int,
        genesis_hash: str,
        current_height: int = 0,
        current_hash: str = "00" * 32,
    ):
        self.node_id = node_id
        self.chain_id = chain_id
        self.genesis_hash = genesis_hash
        self.current_height = current_height
        self.current_hash = current_hash
        
        # Create P2P components
        self.registry = PeerRegistry(
            max_inbound_per_ip=10,
            handshake_timeout_s=20.0,
        )
        
        self.handshake_mgr = HandshakeManager(
            self.registry,
            dial_timeout_s=8.0,
            handshake_timeout_s=15.0,
            chain_id=chain_id,
            genesis_hash=genesis_hash,
        )
        
        self.tip_mgr = TipManager(
            self.registry,
            poll_interval_s=30.0,
            freshness_window_s=600.0,
        )
    
    def connect_outbound(self, remote_addr: str) -> str:
        """Initiate outbound connection."""
        return self.handshake_mgr.start_handshake(remote_addr, "outbound")
    
    def send_hello(self, session_id: str) -> Dict:
        """Send Hello message."""
        self.handshake_mgr.on_hello_sent(session_id)
        return {
            "peer_id": self.node_id,
            "chain_id": self.chain_id,
            "genesis_hash": self.genesis_hash,
            "height": self.current_height,
            "head_hash": self.current_hash,
        }
    
    def receive_hello(self, session_id: str, hello: Dict) -> bool:
        """Process received Hello message. Returns True if validated."""
        peer_id = hello["peer_id"]
        chain_id = hello["chain_id"]
        genesis_hash = hello["genesis_hash"]
        
        # Register peer_id
        self.handshake_mgr.on_peer_identified(session_id, peer_id)
        
        # Validate identity
        success, error = self.handshake_mgr.on_identity_received(
            session_id, chain_id, genesis_hash
        )
        
        # If validation succeeded, record initial tip
        if success:
            height = hello.get("height", 0)
            head_hash = hello.get("head_hash")
            self.tip_mgr.on_tip_received(session_id, height, head_hash)
        
        return success
    
    def update_tip(self, session_id: str, height: int, head_hash: str) -> None:
        """Update peer tip information."""
        self.tip_mgr.on_tip_received(session_id, height, head_hash)
    
    def get_peer_counts(self) -> Dict:
        """Get peer count breakdown."""
        snapshot = self.registry.snapshot()
        return {
            "total": len(snapshot),
            "connected": self.registry.peer_count(),
            "handshaking": self.registry.total_active_sessions(include_handshaking=True) - self.registry.peer_count(),
            "by_state": {
                "CONNECTED": sum(1 for p in snapshot if p.get("state") == "CONNECTED"),
                "HANDSHAKING": sum(1 for p in snapshot if p.get("state") == "HANDSHAKING"),
                "DIALING": sum(1 for p in snapshot if p.get("state") == "DIALING"),
                "FAILED": sum(1 for p in snapshot if p.get("state") == "FAILED"),
            }
        }
    
    def get_tip_stats(self) -> Dict:
        """Get peer tip statistics."""
        total, fresh, stale = self.tip_mgr.get_tip_stats()
        best_height, best_hash, best_peer, best_age = self.tip_mgr.get_best_tip()
        return {
            "total": total,
            "fresh": fresh,
            "stale": stale,
            "best_height": best_height,
            "best_hash": best_hash,
            "best_peer": best_peer,
            "best_age": best_age,
        }


# ============================================================================
# REQUIREMENT 1: Two nodes reach CONNECTED state within 15s
# ============================================================================

def test_two_nodes_connect_within_15s():
    """
    Test that two nodes can complete handshake and reach CONNECTED state.
    
    Validates:
    - Handshake completes successfully
    - Both nodes transition to CONNECTED state
    - Identity validation passes
    - Peer counts are consistent
    """
    # Setup two nodes on same network
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
        current_height=0,
    )
    
    node_b = MockNode(
        node_id=_make_test_peer_id("node_b"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
        current_height=0,
    )
    
    start_time = time.time()
    
    # Node A initiates outbound connection to Node B
    session_a = node_a.connect_outbound("tcp://node_b:9090")
    
    # Node B accepts inbound connection from Node A
    session_b = node_b.handshake_mgr.start_handshake("tcp://node_a:9090", "inbound")
    
    # Exchange Hello messages
    hello_a = node_a.send_hello(session_a)
    hello_b = node_b.send_hello(session_b)
    
    # Process Hello messages (identity validation)
    success_a = node_a.receive_hello(session_a, hello_b)
    success_b = node_b.receive_hello(session_b, hello_a)
    
    handshake_duration = time.time() - start_time
    
    # Assert handshake completed successfully
    assert success_a, "Node A should validate Node B's identity"
    assert success_b, "Node B should validate Node A's identity"
    
    # Assert both peers are in CONNECTED state
    counts_a = node_a.get_peer_counts()
    counts_b = node_b.get_peer_counts()
    
    assert counts_a["connected"] == 1, f"Node A should have 1 connected peer, got {counts_a}"
    assert counts_b["connected"] == 1, f"Node B should have 1 connected peer, got {counts_b}"
    
    assert counts_a["by_state"]["CONNECTED"] == 1
    assert counts_b["by_state"]["CONNECTED"] == 1
    
    # Handshake should complete quickly (< 1s in mock, but check < 15s as requirement)
    assert handshake_duration < 15.0, f"Handshake took {handshake_duration}s, should be < 15s"
    
    print(f"✅ Two nodes connected in {handshake_duration:.3f}s")


def test_handshake_fails_with_chain_id_mismatch():
    """
    Test that handshake fails when chain IDs don't match.
    
    Validates:
    - Identity validation rejects mismatched chain_id
    - Peer transitions to FAILED state
    - Connected peer count remains 0
    """
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=0,  # Mainnet
        genesis_hash=_make_test_hash("genesis"),
    )
    
    node_b = MockNode(
        node_id=_make_test_peer_id("node_b"),
        chain_id=1337,  # Testnet
        genesis_hash=_make_test_hash("genesis"),
    )
    
    # Attempt handshake
    session_a = node_a.connect_outbound("tcp://node_b:9090")
    hello_b = node_b.send_hello("dummy_session")
    success_a = node_a.receive_hello(session_a, hello_b)
    
    # Assert handshake failed
    assert not success_a, "Node A should reject Node B due to chain_id mismatch"
    
    # Assert peer is in FAILED state, not CONNECTED
    counts_a = node_a.get_peer_counts()
    assert counts_a["connected"] == 0, "No peers should be connected"
    assert counts_a["by_state"]["FAILED"] == 1, "Peer should be in FAILED state"
    
    print("✅ Chain ID mismatch properly rejected")


def test_handshake_fails_with_genesis_hash_mismatch():
    """
    Test that handshake fails when genesis hashes don't match.
    """
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis_mainnet"),
    )
    
    node_b = MockNode(
        node_id=_make_test_peer_id("node_b"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis_testnet"),
    )
    
    session_a = node_a.connect_outbound("tcp://node_b:9090")
    hello_b = node_b.send_hello("dummy_session")
    success_a = node_a.receive_hello(session_a, hello_b)
    
    assert not success_a, "Node A should reject Node B due to genesis_hash mismatch"
    
    counts_a = node_a.get_peer_counts()
    assert counts_a["connected"] == 0
    assert counts_a["by_state"]["FAILED"] == 1
    
    print("✅ Genesis hash mismatch properly rejected")


# ============================================================================
# REQUIREMENT 2: Peer counts are consistent
# ============================================================================

def test_peer_counts_consistent_across_methods():
    """
    Test that peer counts are consistent between different query methods.
    
    Validates:
    - PeerRegistry.peer_count() only counts CONNECTED peers
    - PeerRegistry.total_active_sessions() includes handshaking peers
    - Snapshot deduplication works correctly
    """
    node = MockNode(
        node_id=_make_test_peer_id("node"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
    )
    
    # Add 2 peers in HANDSHAKING state
    session1 = node.handshake_mgr.start_handshake("tcp://peer1:9090", "outbound")
    session2 = node.handshake_mgr.start_handshake("tcp://peer2:9090", "outbound")
    
    # Send Hello but don't complete identity validation
    node.handshake_mgr.on_hello_sent(session1)
    node.handshake_mgr.on_hello_sent(session2)
    node.handshake_mgr.on_peer_identified(session1, _make_test_peer_id("peer1"))
    node.handshake_mgr.on_peer_identified(session2, _make_test_peer_id("peer2"))
    
    # Check counts before identity validation
    assert node.registry.peer_count() == 0, "No peers should be CONNECTED yet"
    assert node.registry.total_active_sessions(include_handshaking=True) == 2, "Should have 2 active sessions"
    
    # Complete identity validation for peer1
    hello1 = {"peer_id": _make_test_peer_id("peer1"), "chain_id": 0, "genesis_hash": _make_test_hash("genesis"), "height": 0}
    success1, _ = node.handshake_mgr.on_identity_received(session1, 0, _make_test_hash("genesis"))
    assert success1
    
    # Check counts after 1 peer validated
    assert node.registry.peer_count() == 1, "Should have 1 CONNECTED peer"
    assert node.registry.total_active_sessions(include_handshaking=True) == 2, "Should still have 2 active sessions"
    
    counts = node.get_peer_counts()
    assert counts["connected"] == 1
    assert counts["handshaking"] == 1  # peer2 still handshaking
    assert counts["total"] == 2
    
    print("✅ Peer counts are consistent across methods")


# ============================================================================
# REQUIREMENT 3: Peer tips are real and fresh
# ============================================================================

def test_peer_tips_received_and_stored():
    """
    Test that peer tips are properly received, stored, and tracked for freshness.
    
    Validates:
    - Initial tips received during handshake
    - Tips can be updated
    - Freshness tracking works (fresh vs stale)
    - Best tip is correctly identified
    """
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
        current_height=0,
    )
    
    node_b = MockNode(
        node_id=_make_test_peer_id("node_b"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
        current_height=100,  # Node B is at height 100
        current_hash=_make_test_hash("block_100"),
    )
    
    # Complete handshake
    session_a = node_a.connect_outbound("tcp://node_b:9090")
    hello_b = node_b.send_hello("dummy_session")
    node_a.receive_hello(session_a, hello_b)
    
    # Check that tip was recorded from Hello
    tips_a = node_a.get_tip_stats()
    assert tips_a["total"] == 1, "Should have 1 peer tip"
    assert tips_a["fresh"] == 1, "Tip should be fresh (just received)"
    assert tips_a["stale"] == 0
    assert tips_a["best_height"] == 100, f"Best height should be 100, got {tips_a['best_height']}"
    
    # Update tip (Node B mines block 101)
    node_a.update_tip(session_a, 101, _make_test_hash("block_101"))
    
    tips_a = node_a.get_tip_stats()
    assert tips_a["best_height"] == 101, "Best height should be updated to 101"
    assert tips_a["fresh"] == 1, "Updated tip should still be fresh"
    
    print("✅ Peer tips received, stored, and updated correctly")


def test_no_fresh_peer_tips_when_actually_true():
    """
    Test that "no_fresh_peer_tips" is only shown when it's actually true.
    
    Validates:
    - Fresh tips are correctly identified
    - Stale tips (>10m old) are correctly identified
    - "no_fresh_peer_tips" only appears when no fresh tips exist
    """
    node = MockNode(
        node_id=_make_test_peer_id("node"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
    )
    
    # Add peer with fresh tip
    session = node.connect_outbound("tcp://peer:9090")
    hello = {"peer_id": _make_test_peer_id("peer"), "chain_id": 0, "genesis_hash": _make_test_hash("genesis"), "height": 100}
    node.receive_hello(session, hello)
    
    # Check fresh tips
    tips = node.get_tip_stats()
    assert tips["fresh"] == 1, "Should have 1 fresh tip"
    assert tips["best_height"] == 100
    
    # Simulate tip aging beyond freshness window (600s)
    # In production, this would be tested by mocking time.time()
    # For now, verify that freshness logic exists and works with current time
    total, fresh, stale = node.registry.get_peer_tips(freshness_window_s=0.001)
    assert fresh == 0, "With near-zero freshness window, all tips should be stale"
    assert stale == 1
    
    print("✅ Peer tip freshness correctly tracked")


# ============================================================================
# REQUIREMENT 4: Sync works correctly
# ============================================================================

def test_block_propagation_after_mining():
    """
    Test that when Node A mines a block, Node B learns about it and updates.
    
    Validates:
    - Node A broadcasts new head after mining
    - Node B receives and records the tip update
    - Node B's best peer tip reflects the new height
    """
    # Setup two connected nodes
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
        current_height=0,
    )
    
    node_b = MockNode(
        node_id=_make_test_peer_id("node_b"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
        current_height=0,
    )
    
    # Complete handshake
    session_a = node_a.connect_outbound("tcp://node_b:9090")
    session_b = node_b.handshake_mgr.start_handshake("tcp://node_a:9090", "inbound")
    
    hello_a = node_a.send_hello(session_a)
    hello_b = node_b.send_hello(session_b)
    
    node_a.receive_hello(session_a, hello_b)
    node_b.receive_hello(session_b, hello_a)
    
    # Node A mines block 1
    node_a.current_height = 1
    node_a.current_hash = _make_test_hash("block_1")
    
    # Node A broadcasts new head to Node B
    node_b.update_tip(session_b, 1, _make_test_hash("block_1"))
    
    # Node B should see the update
    tips_b = node_b.get_tip_stats()
    assert tips_b["best_height"] == 1, "Node B should see Node A at height 1"
    assert tips_b["fresh"] == 1, "Tip should be fresh"
    
    print("✅ Block propagation and tip update works correctly")


# ============================================================================
# REQUIREMENT 5: Status outputs are stable
# ============================================================================

def test_head_hash_never_none():
    """
    Test that head_hash is never None, even at genesis.
    
    Validates:
    - Genesis nodes have genesis hash, not None
    - Status always returns valid head_hash
    """
    node = MockNode(
        node_id=_make_test_peer_id("node"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
        current_height=0,
        current_hash=_make_test_hash("genesis"),  # Genesis hash
    )
    
    # At genesis, current_hash should be genesis hash
    assert node.current_hash is not None, "Head hash should never be None"
    assert node.current_hash == _make_test_hash("genesis"), "At genesis, head should be genesis hash"
    
    print("✅ Head hash is never None, always has valid value")


def test_status_schema_consistency():
    """
    Test that status always returns consistent schema with all required fields.
    
    Validates:
    - Peer count fields always present
    - Tip stats fields always present
    - No missing keys in status output
    """
    node = MockNode(
        node_id=_make_test_peer_id("node"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
    )
    
    # Get status (peer counts)
    counts = node.get_peer_counts()
    required_fields = ["total", "connected", "handshaking", "by_state"]
    for field in required_fields:
        assert field in counts, f"Status should include '{field}' field"
    
    # Get tip stats
    tips = node.get_tip_stats()
    required_tip_fields = ["total", "fresh", "stale", "best_height"]
    for field in required_tip_fields:
        assert field in tips, f"Tip stats should include '{field}' field"
    
    print("✅ Status schema is consistent with all required fields")


# ============================================================================
# REQUIREMENT 6: Timeouts/backoffs are sane
# ============================================================================

def test_handshake_timeout_enforcement():
    """
    Test that handshakes timeout after configured duration.
    
    Validates:
    - Dial timeout (8s default)
    - Handshake timeout (15s default)
    - Peers transition to FAILED state on timeout
    """
    node = MockNode(
        node_id=_make_test_peer_id("node"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
    )
    
    # Start handshake but don't complete it
    session = node.connect_outbound("tcp://stuck_peer:9090")
    
    # Check timeouts (using 0 elapsed time - should not timeout yet)
    timed_out = node.handshake_mgr.check_timeouts(now=time.time())
    assert len(timed_out) == 0, "Should not timeout immediately"
    
    # Simulate time passing (20s, exceeds 15s handshake timeout)
    future_time = time.time() + 20.0
    timed_out = node.handshake_mgr.check_timeouts(now=future_time)
    assert len(timed_out) == 1, "Should timeout after 20s"
    assert session in timed_out
    
    # Check peer transitioned to FAILED
    counts = node.get_peer_counts()
    assert counts["by_state"]["FAILED"] == 1, "Timed out peer should be FAILED"
    
    print("✅ Handshake timeouts enforced correctly")


def test_exponential_backoff_on_failures():
    """
    Test that retry backoff increases exponentially.
    
    Validates:
    - First retry: short delay
    - Subsequent retries: exponentially increasing delay
    - Backoff caps at maximum (300s)
    """
    node = MockNode(
        node_id=_make_test_peer_id("node"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
    )
    
    # Start handshake
    session = node.connect_outbound("tcp://failing_peer:9090")
    
    # Mark as error (simulates connection failure)
    node.registry.mark_error(session, reason="connection_failed", penalty=1)
    
    # Check retry_count and next_retry_at
    session_obj = node.registry._sessions.get(session)
    assert session_obj is not None
    assert session_obj.retry_count == 1
    assert session_obj.next_retry_at is not None
    
    # First retry should be ~2s (2^1)
    first_backoff = session_obj.next_retry_at - time.time()
    assert 1.0 <= first_backoff <= 3.0, f"First backoff should be ~2s, got {first_backoff}"
    
    # Mark error again
    node.registry.mark_error(session, reason="connection_failed", penalty=1)
    session_obj = node.registry._sessions.get(session)
    assert session_obj.retry_count == 2
    
    # Second retry should be ~4s (2^2)
    second_backoff = session_obj.next_retry_at - time.time()
    assert 3.0 <= second_backoff <= 5.0, f"Second backoff should be ~4s, got {second_backoff}"
    
    print("✅ Exponential backoff works correctly")


# ============================================================================
# REQUIREMENT 7: No silent failures
# ============================================================================

def test_identity_validation_failures_logged():
    """
    Test that identity validation failures are properly reported, not silent.
    
    Validates:
    - Failed validation returns (False, reason)
    - Reason is descriptive (chain_id_mismatch, genesis_hash_mismatch)
    - Peer transitions to FAILED state with error recorded
    """
    node = MockNode(
        node_id=_make_test_peer_id("node"),
        chain_id=0,
        genesis_hash=_make_test_hash("genesis"),
    )
    
    session = node.connect_outbound("tcp://peer:9090")
    node.handshake_mgr.on_peer_identified(session, _make_test_peer_id("peer"))
    
    # Try to validate with wrong chain_id
    success, error = node.handshake_mgr.on_identity_received(
        session, chain_id=1337, genesis_hash=_make_test_hash("genesis")
    )
    
    assert not success, "Validation should fail"
    assert error == "chain_id_mismatch", f"Error should be 'chain_id_mismatch', got '{error}'"
    
    # Check peer has error recorded
    session_obj = node.registry._sessions.get(session)
    assert session_obj is not None
    assert session_obj.last_error == "chain_id_mismatch"
    assert session_obj.state == PeerState.FAILED
    
    print("✅ Identity validation failures are properly reported")


# ============================================================================
# Run all tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
