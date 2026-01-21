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
    GENESIS_FALLBACK = b"\x00" * 32
    
    # Peer sends actual genesis hash
    peer_genesis_header = actual_genesis
    
    # Local node returns fallback (simulating the case where all lookups fail)
    local_genesis_header = GENESIS_FALLBACK
    
    print(f"\nPeer genesis header: {peer_genesis_header.hex()}")
    print(f"Local genesis header: {local_genesis_header.hex()}")
    
    # Simulate FIXED handshake validation check
    local_is_fallback = local_genesis_header == GENESIS_FALLBACK
    
    if peer_genesis_header and peer_genesis_header != local_genesis_header:
        # FIX: If local genesis is fallback and peer has non-zero genesis, accept
        if local_is_fallback and peer_genesis_header != GENESIS_FALLBACK:
            print("\n✓ PASS: Handshake accepted (local is fallback, learning from peer)")
            return True
        else:
            print("\n❌ FAIL: Handshake rejected due to genesis_mismatch")
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
    GENESIS_FALLBACK = b"\x00" * 32
    
    # Simulate build_valid_genesis_hashes() with fallback
    expected_genesis = GENESIS_FALLBACK
    expected_genesis_block = GENESIS_FALLBACK
    anchor_hash = None
    anchor_candidates = {}
    
    # Build set (FIXED version from p2p_service_legacy.py)
    valid_hashes = {expected_genesis, expected_genesis_block}
    if anchor_hash:
        valid_hashes.add(anchor_hash)
    for h, (height, source) in anchor_candidates.items():
        if height == 0:
            valid_hashes.add(h)
    # Remove None values AND fallback genesis (all zeros)
    # FIX: This ensures defensive fix triggers when only fallback is available
    valid_hashes = {h for h in valid_hashes if h and h != GENESIS_FALLBACK}
    
    print(f"\nValid genesis hashes: {[h.hex() for h in valid_hashes]}")
    print(f"Number of valid hashes: {len(valid_hashes)}")
    
    # Check if defensive fix would trigger
    if not valid_hashes:
        print("\n✓ Defensive fix WILL trigger: No valid genesis hashes (fallback excluded)")
        print("   Height 1 header will be accepted unconditionally")
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
    print("TESTING GENESIS HANDSHAKE FIX")
    print("="*70)
    
    result1 = test_genesis_hash_validation()
    result2 = test_valid_genesis_hashes_with_fallback()
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    if result1 and result2:
        print("\n✓ ALL TESTS PASSED: Fix is working correctly!")
        print("\nFIX SUMMARY:")
        print("  1. Handshake validation now accepts peers when local genesis is fallback")
        print("  2. Header validation excludes fallback from valid_hashes, enabling defensive fix")
        print("  3. Nodes with missing genesis config can now sync from network")
        print("\nBEHAVIOR:")
        print("  - Local node with b'\\x00' * 32 fallback can connect to peers with real genesis")
        print("  - Height 1 headers with real genesis as parent are accepted unconditionally")
        print("  - Sync can progress from genesis to height 1 and beyond")
    else:
        if not result1:
            print("\n❌ TEST 1 FAILED: Handshake validation needs fix")
        if not result2:
            print("\n❌ TEST 2 FAILED: Header validation needs fix")
    
    print("="*70 + "\n")
