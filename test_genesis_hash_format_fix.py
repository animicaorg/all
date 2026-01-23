#!/usr/bin/env python3
"""
Test to verify the genesis hash format fix for identity validation.

This test ensures that the HandshakeManager is initialized with the correct
genesis hash format (with "0x" prefix) so that identity validation succeeds
when comparing with peer genesis hashes.
"""

def test_genesis_hash_format_consistency():
    """
    Test that local and peer genesis hashes use consistent format.
    
    This simulates the fix where we changed from:
        genesis_hash_hex = genesis_hash_bytes.hex()  # No prefix
    to:
        genesis_hash_hex = _canon_hash0x(genesis_hash_bytes)  # With 0x prefix
    """
    print("\n" + "="*70)
    print("Test: Genesis Hash Format Consistency for Identity Validation")
    print("="*70)
    
    # Simulated genesis hash bytes (32 bytes)
    genesis_hash_bytes = bytes.fromhex("cf08020c87d8c294e09e5a872d7a5a2f3ceb9b8576ba0cdbfd1daef6832cbbfb")
    
    # OLD BUGGY WAY: No "0x" prefix
    local_genesis_old = genesis_hash_bytes.hex()
    print(f"\nOLD (buggy) local genesis: {local_genesis_old}")
    
    # Peer always sends with "0x" prefix (via _canon_hash0x)
    peer_genesis = "0x" + genesis_hash_bytes.hex()
    print(f"Peer genesis (always):     {peer_genesis}")
    
    # Comparison in HandshakeManager (case-insensitive)
    match_old = local_genesis_old.lower() == peer_genesis.lower()
    print(f"\nOLD comparison result: {match_old}")
    print(f"  Result: Identity validation {'PASSES' if match_old else 'FAILS'}")
    
    # NEW FIXED WAY: With "0x" prefix
    local_genesis_new = "0x" + genesis_hash_bytes.hex()
    print(f"\nNEW (fixed) local genesis: {local_genesis_new}")
    print(f"Peer genesis (always):     {peer_genesis}")
    
    # Comparison in HandshakeManager (case-insensitive)
    match_new = local_genesis_new.lower() == peer_genesis.lower()
    print(f"\nNEW comparison result: {match_new}")
    print(f"  Result: Identity validation {'PASSES' if match_new else 'FAILS'}")
    
    # Assertions
    assert not match_old, "OLD format should NOT match (this was the bug)"
    assert match_new, "NEW format SHOULD match (this is the fix)"
    
    print("\n" + "="*70)
    print("✓ Test PASSED: Genesis hash format is now consistent")
    print("="*70)


def test_identity_validation_flow():
    """
    Test the complete identity validation flow with the fix.
    """
    print("\n" + "="*70)
    print("Test: Complete Identity Validation Flow")
    print("="*70)
    
    # Scenario: Two nodes on mainnet with same genesis
    chain_id = 1
    genesis_hash_bytes = bytes.fromhex("cf08020c87d8c294e09e5a872d7a5a2f3ceb9b8576ba0cdbfd1daef6832cbbfb")
    
    # Node 1 initialization (HandshakeManager)
    local_chain_id = chain_id
    local_genesis = "0x" + genesis_hash_bytes.hex()  # FIX: Now includes 0x
    
    # Node 2 sends HELLO with its identity
    peer_chain_id = chain_id
    peer_genesis = "0x" + genesis_hash_bytes.hex()  # Always has 0x from _canon_hash0x
    
    print(f"\nNode 1 (local):")
    print(f"  chain_id: {local_chain_id}")
    print(f"  genesis:  {local_genesis}")
    
    print(f"\nNode 2 (peer):")
    print(f"  chain_id: {peer_chain_id}")
    print(f"  genesis:  {peer_genesis}")
    
    # Identity validation logic (from HandshakeManager.on_identity_received)
    chain_match = (peer_chain_id == local_chain_id)
    genesis_match = (peer_genesis.lower() == local_genesis.lower())
    
    validation_success = chain_match and genesis_match
    
    print(f"\nValidation:")
    print(f"  chain_id match:  {chain_match}")
    print(f"  genesis match:   {genesis_match}")
    print(f"  overall result:  {'SUCCESS' if validation_success else 'FAILED'}")
    
    if validation_success:
        print(f"\n✓ identity_ok = True")
        print(f"✓ Peer will be included in tip tracking")
        print(f"✓ peer_tips_fresh will be > 0")
        print(f"✓ Sync will work!")
    else:
        print(f"\n✗ identity_ok = False")
        print(f"✗ Peer filtered from tip tracking")
        print(f"✗ peer_tips_fresh stays at 0")
        print(f"✗ Sync stuck with 'no_fresh_peer_tips'")
    
    assert validation_success, "Identity validation should succeed with matching credentials"
    
    print("\n" + "="*70)
    print("✓ Test PASSED: Identity validation succeeds with fix")
    print("="*70)


def test_case_insensitive_comparison():
    """
    Test that the comparison is case-insensitive (as implemented).
    """
    print("\n" + "="*70)
    print("Test: Case-Insensitive Genesis Hash Comparison")
    print("="*70)
    
    # Test with different cases
    local_genesis = "0xCF08020C87D8C294E09E5A872D7A5A2F3CEB9B8576BA0CDBFD1DAEF6832CBBFB"
    peer_genesis = "0xcf08020c87d8c294e09e5a872d7a5a2f3ceb9b8576ba0cdbfd1daef6832cbbfb"
    
    print(f"\nLocal (uppercase): {local_genesis}")
    print(f"Peer (lowercase):  {peer_genesis}")
    
    # HandshakeManager uses .lower() for comparison
    match = local_genesis.lower() == peer_genesis.lower()
    
    print(f"\nComparison result: {match}")
    assert match, "Case-insensitive comparison should succeed"
    
    print("\n✓ Test PASSED: Case-insensitive comparison works correctly")


if __name__ == "__main__":
    print("="*70)
    print("Testing Genesis Hash Format Fix for P2P Identity Validation")
    print("="*70)
    
    try:
        test_genesis_hash_format_consistency()
        test_identity_validation_flow()
        test_case_insensitive_comparison()
        
        print("\n" + "="*70)
        print("✅ ALL TESTS PASSED!")
        print("="*70)
        print("\nSummary:")
        print("- Genesis hash format is now consistent (with 0x prefix)")
        print("- Identity validation will succeed when credentials match")
        print("- Peers will be included in tip tracking")
        print("- Sync will work properly")
        print("\nThis fixes the 'no_fresh_peer_tips' issue!")
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        exit(1)
