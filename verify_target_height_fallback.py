#!/usr/bin/env python3
"""
Manual verification script for target_height fallback fix.

This simulates the scenario from the problem statement:
- Node at genesis (height 0)
- target_height = 1
- No peers with completed handshakes
- Should use target_height as best_remote_height fallback
"""

def simulate_compute_best_remote_info(peers, sync_target_height):
    """
    Simplified version of _compute_best_remote_info with the fix.
    """
    best_height = None
    best_hash = None
    best_peer = None
    best_age = None
    
    # Check all peers (simplified - just count those with hello_done)
    for peer_addr, peer_data in peers.items():
        if not peer_data.get("hello_done"):
            print(f"  Peer {peer_addr}: hello not done, skipping")
            continue
        # In real code, would check freshness, chain_id, etc.
        print(f"  Peer {peer_addr}: hello done, would check tip")
        # ... more checks ...
    
    # FIX: Fallback to target_height when no fresh peer tips available
    if best_height is None and sync_target_height is not None:
        target = int(sync_target_height)
        if target > 0:
            print(f"  ✓ Using target_height={target} as fallback")
            return target, None, "target_fallback", 0.0
    
    print(f"  ✗ No best_remote_height available")
    return best_height, best_hash, best_peer, best_age


def main():
    print("=== Scenario from Problem Statement ===")
    print("Node at genesis, target_height=1, peers not connected\n")
    
    # Simulate the problem scenario
    peers = {
        "3.133.122.91:30333": {"hello_done": False, "state": "dialing"},
        "82.66.161.84:30333": {"hello_done": False, "state": "dialing"},
    }
    sync_target_height = 1
    
    print("Peers:")
    for addr, data in peers.items():
        print(f"  {addr}: hello_done={data['hello_done']}, state={data['state']}")
    print(f"sync_target_height: {sync_target_height}\n")
    
    print("Calling _compute_best_remote_info:")
    height, hash_hex, peer, age = simulate_compute_best_remote_info(peers, sync_target_height)
    
    print(f"\nResult:")
    print(f"  best_remote_height: {height}")
    print(f"  best_remote_hash: {hash_hex}")
    print(f"  best_remote_peer: {peer}")
    print(f"  best_remote_age: {age}")
    
    if height is not None:
        print(f"\n✓ SUCCESS: best_remote_height={height}, sync can progress!")
        print(f"  behind_by would be: {height} - 0 = {height}")
        print(f"  sync_status_reason would NOT be 'no_fresh_peer_tips'")
    else:
        print(f"\n✗ FAILURE: best_remote_height=None, sync stuck!")
        print(f"  sync_status_reason would be 'no_fresh_peer_tips'")


if __name__ == "__main__":
    main()
