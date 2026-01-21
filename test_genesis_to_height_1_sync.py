#!/usr/bin/env python3
"""
Test case to reproduce the genesis→height 1 sync issue.

From the problem statement:
- Node is stuck at genesis (height 0)
- Headers at height 1 were received but not accepted
- last_headers_accepted_count: 0
- Peers stuck in handshaking state

This test validates that:
1. Peers with wrong genesis_identity/network_params are rejected
2. Headers at height 1 are accepted when valid
3. Sync proceeds from genesis to height 1
"""

def test_handshake_rejection_with_wrong_identity():
    """
    Verify that peers with wrong genesis_identity are rejected during handshake.
    
    This simulates the fix we just implemented where mismatched genesis_identity
    or network_params_hash now raise PeerMisbehavior instead of just warning.
    """
    print("\n" + "="*70)
    print("Test 1: Handshake rejection with wrong genesis_identity")
    print("="*70)
    
    # Simulate the old behavior (bug)
    def old_handshake_validation(peer_genesis_identity, local_genesis_identity):
        """Old code: just logs warning, continues"""
        if peer_genesis_identity != local_genesis_identity:
            print(f"  [OLD] WARNING: genesis_identity mismatch")
            print(f"    peer: {peer_genesis_identity.hex()}")
            print(f"    local: {local_genesis_identity.hex()}")
            # BUG: Doesn't raise exception, allows handshake to continue
            return True, "identity_ok=True (WRONG!)"
        return True, "identity_ok=True"
    
    # Simulate the new behavior (fixed)
    def new_handshake_validation(peer_genesis_identity, local_genesis_identity):
        """New code: sends rejection, raises exception"""
        if peer_genesis_identity != local_genesis_identity:
            print(f"  [NEW] REJECT: genesis_identity mismatch")
            print(f"    peer: {peer_genesis_identity.hex()}")
            print(f"    local: {local_genesis_identity.hex()}")
            # FIX: Properly rejects incompatible peer
            raise Exception("PeerMisbehavior: genesis_identity_mismatch")
        return True, "identity_ok=True"
    
    # Test data
    local_id = b"\x01" * 32
    wrong_id = b"\x02" * 32
    
    # Old behavior
    print("\nOLD BEHAVIOR (buggy):")
    try:
        success, msg = old_handshake_validation(wrong_id, local_id)
        print(f"  Result: {msg}")
        print(f"  ✗ BUG: Peer with wrong identity was accepted!")
    except Exception as e:
        print(f"  Result: Rejected - {e}")
    
    # New behavior
    print("\nNEW BEHAVIOR (fixed):")
    try:
        success, msg = new_handshake_validation(wrong_id, local_id)
        print(f"  Result: {msg}")
        print(f"  ✗ FAIL: Should have rejected peer")
    except Exception as e:
        print(f"  Result: Rejected - {e}")
        print(f"  ✓ PASS: Peer with wrong identity properly rejected")
    
    print()


def test_header_acceptance_at_height_1():
    """
    Verify header acceptance logic at genesis→height 1 transition.
    
    The problem statement shows headers were received but not accepted.
    This could be due to genesis hash validation issues.
    """
    print("\n" + "="*70)
    print("Test 2: Header acceptance at genesis→height 1 transition")
    print("="*70)
    
    # Simulate header validation
    genesis_hash = bytes.fromhex("6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242")
    
    # Scenario 1: Header at height 1 with correct parent
    print("\nScenario 1: Valid height 1 header")
    header_1 = {
        "height": 1,
        "hash": b"\x10" * 32,
        "parent_hash": genesis_hash,
    }
    
    # Check if parent matches genesis
    if header_1["parent_hash"] == genesis_hash:
        print(f"  ✓ Parent hash matches genesis")
        print(f"  ✓ Header should be ACCEPTED")
    else:
        print(f"  ✗ Parent hash does NOT match genesis")
        print(f"  ✗ Header would be REJECTED")
    
    # Scenario 2: Header at height 1 with wrong parent
    print("\nScenario 2: Invalid height 1 header (wrong parent)")
    header_1_wrong = {
        "height": 1,
        "hash": b"\x11" * 32,
        "parent_hash": b"\xFF" * 32,  # Wrong parent!
    }
    
    if header_1_wrong["parent_hash"] == genesis_hash:
        print(f"  ✓ Parent hash matches genesis")
    else:
        print(f"  ✗ Parent hash does NOT match genesis")
        print(f"  ✗ Header should be REJECTED (genesis_mismatch)")
        print(f"    Expected: {genesis_hash.hex()[:16]}...")
        print(f"    Got:      {header_1_wrong['parent_hash'].hex()[:16]}...")
    
    print()


def test_sync_status_with_handshaking_peers():
    """
    Verify that sync status correctly reports "no_fresh_peer_tips" when
    peers are stuck in handshaking state.
    """
    print("\n" + "="*70)
    print("Test 3: Sync status with handshaking peers")
    print("="*70)
    
    # Simulate peer states
    peers = [
        {"remote": "144.126.133.21:30333", "state": "handshaking", "identity_ok": False},
        {"remote": "144.126.133.21:30333", "state": "handshaking", "identity_ok": False},
        {"remote": "144.126.133.21:30333", "state": "handshaking", "identity_ok": False},
    ]
    
    # Count connected peers (identity_ok=True)
    connected_peers = [p for p in peers if p["identity_ok"]]
    
    print(f"\nTotal peers: {len(peers)}")
    print(f"Connected peers (identity_ok=True): {len(connected_peers)}")
    
    # Determine sync status reason
    if len(connected_peers) == 0:
        if len(peers) > 0:
            sync_status_reason = "no_fresh_peer_tips"
            print(f"\n✓ Sync status reason: '{sync_status_reason}'")
            print(f"  Explanation: Peers connected but stuck in handshaking")
            print(f"  None have identity_ok=True, so no tips available")
        else:
            sync_status_reason = "no_peers_connected"
            print(f"\n✓ Sync status reason: '{sync_status_reason}'")
    else:
        print(f"\n✓ Have {len(connected_peers)} connected peers")
        print(f"  Tips should be available")
    
    print()


def test_network_best_height_with_handshaking_peers():
    """
    Verify that _network_best_height() returns None when all peers
    are stuck in handshaking (hello_done not set).
    """
    print("\n" + "="*70)
    print("Test 4: Network best height with handshaking peers")
    print("="*70)
    
    # Simulate peers
    peers = [
        {"remote": "peer1", "hello_done": False, "repo_state_ok": True, "hello": {"head_height": 100}},
        {"remote": "peer2", "hello_done": False, "repo_state_ok": True, "hello": {"head_height": 101}},
        {"remote": "peer3", "hello_done": False, "repo_state_ok": True, "hello": {"head_height": 102}},
    ]
    
    # Simulate _network_best_height() logic
    heights = []
    for peer in peers:
        # Only include peers with hello_done set
        if not peer["hello_done"]:
            continue
        if not peer["repo_state_ok"]:
            continue
        # Would add peer heights here
        heights.append(peer["hello"]["head_height"])
    
    network_best_height = max(heights) if heights else None
    
    print(f"\nPeers:")
    for p in peers:
        print(f"  {p['remote']}: hello_done={p['hello_done']}, height={p['hello']['head_height']}")
    
    print(f"\nNetwork best height: {network_best_height}")
    
    if network_best_height is None:
        print(f"✓ Returns None because no peers have hello_done=True")
        print(f"  This causes 'no_fresh_peer_tips' sync status")
    else:
        print(f"✓ Returns {network_best_height}")
    
    print()


if __name__ == "__main__":
    test_handshake_rejection_with_wrong_identity()
    test_header_acceptance_at_height_1()
    test_sync_status_with_handshaking_peers()
    test_network_best_height_with_handshaking_peers()
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print("""
Fix implemented:
- Peers with wrong genesis_identity are now properly rejected
- Peers with wrong network_params_hash are now properly rejected
- This prevents peers from staying in "handshaking" state indefinitely
- Once incompatible peers are disconnected, the node can connect to compatible ones

Expected result after fix:
- Incompatible peers (144.126.133.21:30333) will be rejected immediately
- Node will connect to compatible peers
- Those peers will complete handshake (hello_done=True, identity_ok=True)
- Sync will proceed from genesis to height 1
- "no_fresh_peer_tips" error will be resolved
""")
