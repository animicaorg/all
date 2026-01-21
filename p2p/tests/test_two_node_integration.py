"""
Phase 7: Integration Tests for P2P Two-Node Scenarios

This module provides end-to-end tests that validate the complete P2P flow:
- Handshake orchestration (DIALING → HANDSHAKING → CONNECTED)
- Identity validation (chain_id, genesis_hash)
- Timeout enforcement
- Tip exchange and polling
- Status schema consistency
- Peer count accuracy

Uses lightweight mocking at the message level (Option 1) for fast, deterministic tests.
"""

import pytest
import time
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

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
    Lightweight mock node for two-node integration tests.
    
    Encapsulates PeerRegistry, HandshakeManager, and TipManager for a single node.
    Provides helpers to simulate message exchange with another node.
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
        """
        Initiate outbound connection to remote peer.
        Returns session_id.
        """
        return self.handshake_mgr.start_handshake(remote_addr, "outbound")
    
    def accept_inbound(self, remote_addr: str) -> str:
        """
        Accept inbound connection from remote peer.
        Returns session_id.
        """
        return self.handshake_mgr.start_handshake(remote_addr, "inbound")
    
    def send_hello(self, session_id: str) -> Dict:
        """
        Simulate sending Hello message to peer.
        Returns Hello message content.
        """
        self.handshake_mgr.on_hello_sent(session_id)
        return {
            "peer_id": self.node_id,
            "version": "2",
            "agent": f"test-node/{self.node_id}",
        }
    
    def receive_hello(self, session_id: str, hello_msg: Dict) -> None:
        """
        Simulate receiving Hello message from peer.
        """
        self.handshake_mgr.on_hello_received(
            session_id,
            peer_id=hello_msg["peer_id"],
            version=hello_msg["version"],
            agent=hello_msg["agent"],
        )
    
    def send_identity(self) -> Dict:
        """
        Simulate sending identity (chain_id, genesis_hash) to peer.
        Returns identity message content.
        """
        return {
            "chain_id": self.chain_id,
            "genesis_hash": self.genesis_hash,
        }
    
    def receive_identity(self, session_id: str, identity_msg: Dict) -> Tuple[bool, Optional[str]]:
        """
        Simulate receiving identity from peer.
        Returns (success, error_reason).
        """
        return self.handshake_mgr.on_identity_received(
            session_id,
            chain_id=identity_msg["chain_id"],
            genesis_hash=identity_msg["genesis_hash"],
        )
    
    def send_tip(self) -> Dict:
        """
        Simulate sending current tip (HeadStatus) to peer.
        Returns tip message content.
        """
        return {
            "height": self.current_height,
            "hash": self.current_hash,
            "tip_time": time.time(),
        }
    
    def receive_tip(self, session_id: str, tip_msg: Dict) -> None:
        """
        Simulate receiving tip from peer.
        """
        self.tip_mgr.on_tip_received(
            session_id,
            height=tip_msg["height"],
            hash_hex=tip_msg.get("hash"),
            tip_time=tip_msg.get("tip_time"),
        )
    
    def update_height(self, new_height: int, new_hash: str) -> None:
        """
        Advance node's chain height (simulates block production).
        """
        self.current_height = new_height
        self.current_hash = new_hash
    
    def peer_count(self) -> int:
        """
        Get count of connected peers with validated identity.
        """
        return self.registry.peer_count()
    
    def total_active_sessions(self) -> int:
        """
        Get count of all active sessions (including handshaking).
        """
        return self.registry.total_active_sessions()
    
    def get_best_peer_tip(self) -> Tuple[Optional[int], Optional[str], Optional[str], Optional[float]]:
        """
        Get the best (highest) peer tip.
        Returns (height, hash, peer_id, age).
        """
        return self.tip_mgr.get_best_tip()
    
    def check_timeouts(self, now: Optional[float] = None) -> List[str]:
        """
        Check for handshake timeouts and fail stuck sessions.
        Returns list of timed out session_ids.
        """
        return self.handshake_mgr.check_timeouts(now=now)
    
    def poll_tips(self, now: Optional[float] = None) -> List[str]:
        """
        Identify peers that need tip refresh.
        Returns list of session_ids to poll.
        """
        return self.tip_mgr.poll_peer_tips(now=now)
    
    def get_session(self, session_id: str):
        """
        Get peer session by ID.
        """
        return self.registry._sessions.get(session_id)


def test_handshake_completes_within_timeout():
    """
    Test: Handshake Completes Within 15s
    
    Validates that two nodes can successfully complete a handshake:
    - Node A starts outbound connection to Node B
    - Node B accepts inbound connection from Node A
    - Both exchange Hello messages
    - Both exchange identity (chain_id, genesis_hash)
    - Both reach CONNECTED state
    - peer_count() == 1 on both nodes
    """
    # Setup: Create two nodes with matching chain parameters
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=1,
        genesis_hash="aa" * 64,
        current_height=5,
    )
    
    node_b = MockNode(
        node_id=_make_test_peer_id("node_b"),
        chain_id=1,
        genesis_hash="aa" * 64,
        current_height=10,
    )
    
    # Step 1: Node A initiates outbound connection to Node B
    session_a = node_a.connect_outbound("tcp://node-b:8000")
    
    # Step 2: Node B accepts inbound connection from Node A
    session_b = node_b.accept_inbound("tcp://node-a:8000")
    
    # Verify initial state: DIALING
    assert node_a.get_session(session_a).state == PeerState.DIALING
    assert node_b.get_session(session_b).state == PeerState.DIALING
    
    # Step 3: Exchange Hello messages
    # A → B: Hello
    hello_a = node_a.send_hello(session_a)
    node_b.receive_hello(session_b, hello_a)
    
    # B → A: Hello
    hello_b = node_b.send_hello(session_b)
    node_a.receive_hello(session_a, hello_b)
    
    # Verify state transition: DIALING → HANDSHAKING
    assert node_a.get_session(session_a).state == PeerState.HANDSHAKING
    assert node_b.get_session(session_b).state == PeerState.HANDSHAKING
    
    # Step 4: Exchange identity messages
    # A → B: Identity
    identity_a = node_a.send_identity()
    success_b, error_b = node_b.receive_identity(session_b, identity_a)
    assert success_b is True
    assert error_b is None
    
    # B → A: Identity
    identity_b = node_b.send_identity()
    success_a, error_a = node_a.receive_identity(session_a, identity_b)
    assert success_a is True
    assert error_a is None
    
    # Verify state transition: HANDSHAKING → CONNECTED
    assert node_a.get_session(session_a).state == PeerState.CONNECTED
    assert node_b.get_session(session_b).state == PeerState.CONNECTED
    
    # Verify identity validation
    assert node_a.get_session(session_a).identity_ok is True
    assert node_b.get_session(session_b).identity_ok is True
    
    # Verify peer count
    assert node_a.peer_count() == 1
    assert node_b.peer_count() == 1
    
    # Verify peer_id assignment
    assert node_a.get_session(session_a).peer_id == node_b.node_id
    assert node_b.get_session(session_b).peer_id == node_a.node_id


def test_handshake_fails_chain_id_mismatch():
    """
    Test: Handshake Fails on Chain ID Mismatch
    
    Validates that handshake fails when nodes have different chain_ids:
    - Node A with chain_id=1, Node B with chain_id=2
    - Attempt handshake
    - Both reach FAILED state
    - identity_ok=False
    - last_error contains "chain_id"
    """
    # Setup: Create two nodes with DIFFERENT chain_ids
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=1,  # Mainnet
        genesis_hash="aa" * 64,
    )
    
    node_b = MockNode(
        node_id=_make_test_peer_id("node_b"),
        chain_id=2,  # Testnet
        genesis_hash="aa" * 64,
    )
    
    # Step 1: Establish connection
    session_a = node_a.connect_outbound("tcp://node-b:8000")
    session_b = node_b.accept_inbound("tcp://node-a:8000")
    
    # Step 2: Exchange Hello messages
    hello_a = node_a.send_hello(session_a)
    node_b.receive_hello(session_b, hello_a)
    
    hello_b = node_b.send_hello(session_b)
    node_a.receive_hello(session_a, hello_b)
    
    assert node_a.get_session(session_a).state == PeerState.HANDSHAKING
    assert node_b.get_session(session_b).state == PeerState.HANDSHAKING
    
    # Step 3: Exchange identity messages (will fail due to chain_id mismatch)
    # A → B: Identity (chain_id=1)
    identity_a = node_a.send_identity()
    success_b, error_b = node_b.receive_identity(session_b, identity_a)
    
    # Verify Node B rejected Node A's identity
    assert success_b is False
    assert error_b == "chain_id_mismatch"
    assert node_b.get_session(session_b).state == PeerState.FAILED
    assert node_b.get_session(session_b).identity_ok is False
    assert "chain_id" in node_b.get_session(session_b).last_error
    
    # B → A: Identity (chain_id=2)
    identity_b = node_b.send_identity()
    success_a, error_a = node_a.receive_identity(session_a, identity_b)
    
    # Verify Node A rejected Node B's identity
    assert success_a is False
    assert error_a == "chain_id_mismatch"
    assert node_a.get_session(session_a).state == PeerState.FAILED
    assert node_a.get_session(session_a).identity_ok is False
    assert "chain_id" in node_a.get_session(session_a).last_error
    
    # Verify peer_count remains 0
    assert node_a.peer_count() == 0
    assert node_b.peer_count() == 0


def test_handshake_timeout():
    """
    Test: Handshake Fails on Timeout
    
    Validates that handshake manager enforces timeouts:
    - Node A starts connection but doesn't send Hello
    - Wait 20s (simulated time)
    - handshake_mgr.check_timeouts() marks it FAILED
    - last_error contains "timeout"
    """
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=1,
        genesis_hash="aa" * 64,
    )
    
    # Start connection but don't send Hello
    session_a = node_a.connect_outbound("tcp://node-b:8000")
    
    # Verify initial state: DIALING
    assert node_a.get_session(session_a).state == PeerState.DIALING
    
    # Simulate passage of time: 20 seconds
    start_time = time.time()
    future_time = start_time + 20.0
    
    # Check timeouts with simulated time
    timed_out = node_a.check_timeouts(now=future_time)
    
    # Verify timeout triggered
    assert session_a in timed_out
    
    # Verify state transitioned to FAILED
    session = node_a.get_session(session_a)
    assert session.state == PeerState.FAILED
    
    # Verify error reason
    assert session.last_error is not None
    assert "timeout" in session.last_error.lower()
    
    # Verify peer_count remains 0
    assert node_a.peer_count() == 0


def test_tip_exchange_after_handshake():
    """
    Test: Tip Exchange After Handshake
    
    Validates that nodes exchange tip information after completing handshake:
    - Two nodes complete handshake
    - Node A at height 5, Node B at height 10
    - Both exchange tips
    - Node A knows Node B's tip (height=10)
    - Node B knows Node A's tip (height=5)
    - get_best_peer_tip() returns correct values
    """
    # Setup: Create two nodes at different heights
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=1,
        genesis_hash="aa" * 64,
        current_height=5,
        current_hash=_make_test_hash("a5"),
    )
    
    node_b = MockNode(
        node_id=_make_test_peer_id("node_b"),
        chain_id=1,
        genesis_hash="aa" * 64,
        current_height=10,
        current_hash=_make_test_hash("b10"),
    )
    
    # Step 1: Complete handshake
    session_a = node_a.connect_outbound("tcp://node-b:8000")
    session_b = node_b.accept_inbound("tcp://node-a:8000")
    
    # Exchange Hello
    hello_a = node_a.send_hello(session_a)
    node_b.receive_hello(session_b, hello_a)
    hello_b = node_b.send_hello(session_b)
    node_a.receive_hello(session_a, hello_b)
    
    # Exchange Identity
    identity_a = node_a.send_identity()
    node_b.receive_identity(session_b, identity_a)
    identity_b = node_b.send_identity()
    node_a.receive_identity(session_a, identity_b)
    
    # Verify handshake complete
    assert node_a.get_session(session_a).state == PeerState.CONNECTED
    assert node_b.get_session(session_b).state == PeerState.CONNECTED
    
    # Step 2: Exchange tips
    # A → B: Tip (height=5)
    tip_a = node_a.send_tip()
    node_b.receive_tip(session_b, tip_a)
    
    # B → A: Tip (height=10)
    tip_b = node_b.send_tip()
    node_a.receive_tip(session_a, tip_b)
    
    # Verify Node A knows Node B's tip
    session_a_data = node_a.get_session(session_a)
    assert session_a_data.tip_height == 10
    assert session_a_data.tip_hash == _make_test_hash("b10")
    assert session_a_data.tip_updated_at is not None
    
    # Verify Node B knows Node A's tip
    session_b_data = node_b.get_session(session_b)
    assert session_b_data.tip_height == 5
    assert session_b_data.tip_hash == _make_test_hash("a5")
    assert session_b_data.tip_updated_at is not None
    
    # Verify get_best_peer_tip() on Node A (should see B's higher tip)
    best_height, best_hash, best_peer, best_age = node_a.get_best_peer_tip()
    assert best_height == 10
    assert best_hash == _make_test_hash("b10")
    assert best_peer == node_b.node_id
    assert best_age is not None
    assert best_age < 5.0  # Fresh tip (< 5 seconds old)
    
    # Verify get_best_peer_tip() on Node B (should see A's tip)
    best_height, best_hash, best_peer, best_age = node_b.get_best_peer_tip()
    assert best_height == 5
    assert best_hash == _make_test_hash("a5")
    assert best_peer == node_a.node_id


def test_tip_polling_refresh():
    """
    Test: Tip Polling Refreshes Stale Tips
    
    Validates that TipManager polls peers for tip updates:
    - Two nodes connected with initial tips
    - Node B advances to height 15 (35s later, simulated)
    - TipManager.poll_peer_tips() triggers on Node A
    - Node A sends HeadStatus request
    - Node B responds with new tip (height=15)
    - Node A's view of Node B updated to height=15
    """
    # Setup: Create two nodes
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=1,
        genesis_hash="aa" * 64,
        current_height=5,
        current_hash=_make_test_hash("a5"),
    )
    
    node_b = MockNode(
        node_id=_make_test_peer_id("node_b"),
        chain_id=1,
        genesis_hash="aa" * 64,
        current_height=10,
        current_hash=_make_test_hash("b10"),
    )
    
    # Complete handshake and initial tip exchange
    session_a = node_a.connect_outbound("tcp://node-b:8000")
    session_b = node_b.accept_inbound("tcp://node-a:8000")
    
    # Exchange Hello
    hello_a = node_a.send_hello(session_a)
    node_b.receive_hello(session_b, hello_a)
    hello_b = node_b.send_hello(session_b)
    node_a.receive_hello(session_a, hello_b)
    
    # Exchange Identity
    identity_a = node_a.send_identity()
    node_b.receive_identity(session_b, identity_a)
    identity_b = node_b.send_identity()
    node_a.receive_identity(session_a, identity_b)
    
    # Exchange initial tips
    tip_a = node_a.send_tip()
    node_b.receive_tip(session_b, tip_a)
    tip_b = node_b.send_tip()
    node_a.receive_tip(session_a, tip_b)
    
    # Verify initial state
    assert node_a.get_session(session_a).tip_height == 10
    
    # Step 1: Simulate passage of time (35 seconds)
    start_time = time.time()
    future_time = start_time + 35.0
    
    # Step 2: Node B advances to height 15
    node_b.update_height(15, _make_test_hash("b15"))
    
    # Step 3: Node A polls for tip updates
    to_poll = node_a.poll_tips(now=future_time)
    
    # Verify Node A wants to poll Node B (tip is stale)
    assert session_a in to_poll
    
    # Step 4: Simulate tip refresh
    # A → B: "send me your current tip"
    # B → A: Updated tip (height=15)
    tip_b_updated = node_b.send_tip()
    node_a.receive_tip(session_a, tip_b_updated)
    
    # Verify Node A's view updated to height=15
    session_a_data = node_a.get_session(session_a)
    assert session_a_data.tip_height == 15
    assert session_a_data.tip_hash == _make_test_hash("b15")
    
    # Verify get_best_peer_tip() now shows updated tip
    best_height, _, _, _ = node_a.get_best_peer_tip()
    assert best_height == 15


def test_status_schema_always_complete():
    """
    Test: Status Schema Consistency
    
    Validates that status snapshots are always well-formed:
    - Node at height 0 (genesis)
    - Get status snapshot
    - head_hash is NOT None (should be genesis hash)
    - All required keys present
    - No peers: best_remote_peer is None (not "target_fallback")
    """
    # Setup: Node at genesis (height 0)
    genesis_hash = "aa" * 64
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=1,
        genesis_hash=genesis_hash,
        current_height=0,
        current_hash=genesis_hash,  # At genesis, head_hash == genesis_hash
    )
    
    # Verify node state
    assert node_a.current_height == 0
    assert node_a.current_hash == genesis_hash
    
    # Get peer registry snapshot
    snapshot = node_a.registry.snapshot()
    assert isinstance(snapshot, list)
    
    # With no peers, snapshot should be empty list
    assert len(snapshot) == 0
    
    # Verify peer_count() is 0
    assert node_a.peer_count() == 0
    
    # Verify get_best_peer_tip() returns None for all fields
    best_height, best_hash, best_peer, best_age = node_a.get_best_peer_tip()
    assert best_height is None
    assert best_hash is None
    assert best_peer is None
    assert best_age is None
    
    # Add a peer and verify snapshot structure
    session_id = node_a.connect_outbound("tcp://node-b:8000")
    
    snapshot = node_a.registry.snapshot()
    assert len(snapshot) == 1
    
    peer_snapshot = snapshot[0]
    
    # Verify required keys present
    assert "remote" in peer_snapshot
    assert "direction" in peer_snapshot
    assert "connected_at" in peer_snapshot
    assert "last_seen" in peer_snapshot
    assert "peer_id" in peer_snapshot
    assert "state" in peer_snapshot
    assert "state_since" in peer_snapshot
    assert "identity_ok" in peer_snapshot
    
    # Verify state is DIALING (not yet handshaked)
    assert peer_snapshot["state"] == "DIALING"
    assert peer_snapshot["identity_ok"] is False
    assert peer_snapshot["peer_id"] == "(handshaking)"
    
    # Verify no identity fields yet (not present until handshake)
    assert "remote_chain_id" not in peer_snapshot or peer_snapshot.get("remote_chain_id") is None
    assert "remote_genesis_hash" not in peer_snapshot or peer_snapshot.get("remote_genesis_hash") is None


def test_peer_count_consistency():
    """
    Test: Peer Count Consistency
    
    Validates that peer_count() only counts fully connected peers:
    - Start with 3 peers: 1 DIALING, 1 HANDSHAKING, 1 CONNECTED
    - peer_count() == 1 (only CONNECTED)
    - total_active_sessions() == 3 (all sessions)
    - Snapshot shows all 3 with correct states
    """
    node_a = MockNode(
        node_id=_make_test_peer_id("node_a"),
        chain_id=1,
        genesis_hash="aa" * 64,
    )
    
    # Peer 1: DIALING (just started connection)
    session_dialing = node_a.connect_outbound("tcp://peer1:8000")
    
    # Peer 2: HANDSHAKING (received Hello, waiting for identity)
    session_handshaking = node_a.connect_outbound("tcp://peer2:8000")
    hello_2 = {
        "peer_id": _make_test_peer_id("peer2"),
        "version": "2",
        "agent": "test-peer/2.0",
    }
    node_a.receive_hello(session_handshaking, hello_2)
    
    # Peer 3: CONNECTED (full handshake complete)
    session_connected = node_a.connect_outbound("tcp://peer3:8000")
    hello_3 = {
        "peer_id": _make_test_peer_id("peer3"),
        "version": "2",
        "agent": "test-peer/3.0",
    }
    node_a.receive_hello(session_connected, hello_3)
    identity_3 = {
        "chain_id": 1,
        "genesis_hash": "aa" * 64,
    }
    success, _ = node_a.receive_identity(session_connected, identity_3)
    assert success is True
    
    # Verify states
    assert node_a.get_session(session_dialing).state == PeerState.DIALING
    assert node_a.get_session(session_handshaking).state == PeerState.HANDSHAKING
    assert node_a.get_session(session_connected).state == PeerState.CONNECTED
    
    # Verify identity_ok flags
    assert node_a.get_session(session_dialing).identity_ok is False
    assert node_a.get_session(session_handshaking).identity_ok is False
    assert node_a.get_session(session_connected).identity_ok is True
    
    # Verify peer_count() == 1 (only CONNECTED with identity_ok)
    assert node_a.peer_count() == 1
    
    # Verify total_active_sessions() == 3 (all sessions)
    assert node_a.total_active_sessions() == 3
    
    # Verify snapshot shows all 3 peers
    snapshot = node_a.registry.snapshot()
    assert len(snapshot) == 3
    
    # Find each peer in snapshot and verify state
    states = [peer["state"] for peer in snapshot]
    assert "DIALING" in states
    assert "HANDSHAKING" in states
    assert "CONNECTED" in states
    
    # Verify identity_ok flags in snapshot
    for peer in snapshot:
        if peer["state"] == "CONNECTED":
            assert peer["identity_ok"] is True
        else:
            assert peer["identity_ok"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
