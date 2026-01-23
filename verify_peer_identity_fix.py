#!/usr/bin/env python3
"""Integration test to verify the peer identity_ok bug fix."""

import sys
from p2p.node.handshake import HandshakeManager
from p2p.node.peer_registry import PeerRegistry, PeerState

def test_wrong_chain_id():
    """Test: peer with wrong chain_id is NOT counted as connected."""
    print("=" * 70)
    print("Test: Peer with Wrong Chain ID")
    print("=" * 70)
    
    registry = PeerRegistry()
    manager = HandshakeManager(registry, chain_id=1337, genesis_hash="0xabc123" * 10 + "ab")
    
    print("\n1. Peer connects and sends Hello...")
    session_id = manager.start_handshake("tcp://144.126.133.21:30333", "outbound")
    manager.on_hello_received(session_id, "peer_144_126_133_21", "2", "animica/1.0")
    
    print("2. Peer sends identity with WRONG chain_id (9999)...")
    success, error = manager.on_identity_received(session_id, chain_id=9999, genesis_hash="0xabc123" * 10 + "ab")
    
    session = registry._sessions.get(session_id)
    snapshot_peers = registry.snapshot()
    connected_count = sum(1 for s in snapshot_peers if s.get("state") == "CONNECTED" and s.get("identity_ok"))
    
    print(f"\n3. Results:")
    print(f"   - validation success: {success}")
    print(f"   - peer state: {session.state.value}")
    print(f"   - identity_ok: {session.identity_ok}")
    print(f"   - peers_connected: {connected_count}")
    
    assert not success and error == "chain_id_mismatch"
    assert session.state == PeerState.FAILED and session.identity_ok is False
    assert connected_count == 0
    
    print("\n✓ PASS: Peer correctly rejected, identity_ok=False, connected=0")
    return True

def test_correct_identity():
    """Test: peer WITH correct identity IS counted as connected."""
    print("\n" + "=" * 70)
    print("Test: Peer with Correct Identity")
    print("=" * 70)
    
    registry = PeerRegistry()
    manager = HandshakeManager(registry, chain_id=1337, genesis_hash="0xabc123" * 10 + "ab")
    
    print("\n1. Peer connects and sends Hello...")
    session_id = manager.start_handshake("tcp://144.126.133.21:30333", "outbound")
    manager.on_hello_received(session_id, "peer_144_126_133_21", "2", "animica/1.0")
    
    print("2. Peer sends identity with CORRECT chain_id (1337)...")
    success, error = manager.on_identity_received(session_id, chain_id=1337, genesis_hash="0xabc123" * 10 + "ab")
    
    session = registry._sessions.get(session_id)
    snapshot_peers = registry.snapshot()
    connected_count = sum(1 for s in snapshot_peers if s.get("state") == "CONNECTED" and s.get("identity_ok"))
    
    print(f"\n3. Results:")
    print(f"   - validation success: {success}")
    print(f"   - peer state: {session.state.value}")
    print(f"   - identity_ok: {session.identity_ok}")
    print(f"   - peers_connected: {connected_count}")
    
    assert success and error is None
    assert session.state == PeerState.CONNECTED and session.identity_ok is True
    assert connected_count == 1
    
    print("\n✓ PASS: Peer correctly connected, identity_ok=True, connected=1")
    return True

if __name__ == "__main__":
    try:
        test_wrong_chain_id()
        test_correct_identity()
        print("\n" + "=" * 70)
        print("ALL TESTS PASSED!")
        print("=" * 70)
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
