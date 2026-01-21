#!/usr/bin/env python3
"""
Test to reproduce the genesis handshake issue where peers can't complete handshakes.
"""


def test_genesis_hash_validation():
    """
    Test genesis hash validation logic in handshake.
    
    Simulates the issue where local node has b"\\x00" * 32 as genesis hash fallback,
    but peers have the actual genesis hash, causing handshake failures.
    """
    print("\n" + "="*70)
    print("Test: Genesis Hash Validation in Handshake")
    print("="*70)
    
    # Actual genesis hash from the problem statement
    actual_genesis = bytes.fromhex("6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242")
    
    # Fallback genesis hash (all zeros)
    fallback_genesis = b"\x00" * 32
    
    # Peer sends actual genesis hash
    peer_genesis_header = actual_genesis
    
    # Local node returns fallback (simulating the case where all lookups fail)
    local_genesis_header = fallback_genesis
    
    print(f"\nPeer genesis header: {peer_genesis_header.hex()}")
    print(f"Local genesis header: {local_genesis_header.hex()}")
    
    # Simulate handshake validation check (line 6471 in p2p_service_legacy.py)
    if peer_genesis_header and peer_genesis_header != local_genesis_header:
        print("\n❌ FAIL: Handshake rejected due to genesis_mismatch")
        print("   This is the bug! Local node has fallback genesis hash,")
        print("   but peer has actual genesis hash, causing mismatch.")
        return False
    else:
        print("\n✓ PASS: Handshake accepted")
        return True


def test_valid_genesis_hashes_with_fallback():
    """
    Test that build_valid_genesis_hashes() doesn't include fallback hash.
    """
    print("\n" + "="*70)
    print("Test: Valid Genesis Hashes with Fallback")
    print("="*70)
    
    # Actual genesis hash
    actual_genesis = bytes.fromhex("6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242")
    
    # Fallback genesis hash (all zeros)
    fallback_genesis = b"\x00" * 32
    
    # Simulate build_valid_genesis_hashes() with fallback
    expected_genesis = fallback_genesis
    expected_genesis_block = fallback_genesis
    anchor_hash = None
    anchor_candidates = {}
    
    # Build set (simplified version from line 10390-10410)
    valid_hashes = {expected_genesis, expected_genesis_block}
    if anchor_hash:
        valid_hashes.add(anchor_hash)
    for h, (height, source) in anchor_candidates.items():
        if height == 0:
            valid_hashes.add(h)
    # Remove None values
    valid_hashes = {h for h in valid_hashes if h}
    
    print(f"\nValid genesis hashes: {[h.hex() for h in valid_hashes]}")
    print(f"Number of valid hashes: {len(valid_hashes)}")
    
    # Check if defensive fix would trigger
    if not valid_hashes:
        print("\n✓ Defensive fix would trigger: No valid genesis hashes")
        print("   Height 1 header would be accepted unconditionally")
        return True
    else:
        print("\n❌ Defensive fix would NOT trigger: valid_hashes is not empty")
        print(f"   valid_hashes contains: {list(valid_hashes)[0].hex()}")
        
        # Now check if peer's parent hash would match
        peer_parent_hash = actual_genesis
        if peer_parent_hash in valid_hashes:
            print(f"✓ Peer parent hash matches valid hashes")
            return True
        else:
            print(f"❌ Peer parent hash does NOT match valid hashes")
            print(f"   Peer would be rejected with 'anchor_parent_mismatch'")
            return False


if __name__ == "__main__":
    print("\n" + "="*70)
    print("REPRODUCING GENESIS HANDSHAKE ISSUE")
    print("="*70)
    
    result1 = test_genesis_hash_validation()
    result2 = test_valid_genesis_hashes_with_fallback()
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    if not result1:
        print("\n❌ BUG CONFIRMED: Genesis hash fallback causes handshake failures")
        print("\nROOT CAUSE:")
        print("  1. Local node's genesis hash methods return b'\\x00' * 32 fallback")
        print("  2. Peer sends actual genesis hash in HELLO message")
        print("  3. Handshake validation compares: peer_genesis != local_genesis")
        print("  4. Handshake is rejected with 'genesis_mismatch'")
        print("  5. Peer never completes handshake, stays in 'handshaking' state")
        print("  6. No peer tips available, sync stuck at genesis")
        
        print("\nFIX:")
        print("  Modify handshake validation to be more permissive when local")
        print("  genesis hash is the fallback value (b'\\x00' * 32):")
        print("  - If local genesis is fallback AND peer genesis is non-zero,")
        print("    accept the handshake and learn the genesis hash from peer")
        print("  - This allows nodes with missing genesis config to sync from network")
    
    if not result2:
        print("\n❌ BUG CONFIRMED: Fallback genesis hash blocks header acceptance")
        print("\nROOT CAUSE:")
        print("  1. build_valid_genesis_hashes() includes b'\\x00' * 32 fallback")
        print("  2. Defensive fix checks: 'if not valid_genesis_hashes'")
        print("  3. But valid_hashes = {b'\\x00' * 32} is NOT empty!")
        print("  4. Defensive fix doesn't trigger")
        print("  5. Peer's height 1 header has parent = actual genesis hash")
        print("  6. actual_genesis NOT IN {b'\\x00' * 32}")
        print("  7. Header rejected with 'anchor_parent_mismatch'")
        
        print("\nFIX:")
        print("  Exclude fallback genesis hash (b'\\x00' * 32) from valid_hashes:")
        print("  - After building valid_hashes set, filter out b'\\x00' * 32")
        print("  - This ensures defensive fix triggers when only fallback is available")
        print("  - OR: Accept any parent hash if valid_hashes only contains fallback")
    
    if result1 and result2:
        print("\n✓ No issues detected")
    
    print("="*70 + "\n")
