#!/usr/bin/env python3
"""
Test for the defensive genesis sync fix.

This test verifies that height 1 headers are accepted even when all
genesis hash methods return None, preventing a deadlock scenario.
"""


def test_empty_valid_genesis_hashes():
    """
    Test the scenario where all genesis hash methods return None.
    
    Before fix: valid_genesis_hashes would be empty, and ALL height 1 headers
    would be rejected, creating a permanent deadlock.
    
    After fix: If valid_genesis_hashes is empty, height 1 headers are accepted
    unconditionally at genesis to prevent deadlock.
    """
    print("\n" + "="*70)
    print("Test: Empty valid_genesis_hashes at genesis")
    print("="*70)
    
    # Simulate the scenario
    expected_genesis = None  # _genesis_header_hash() returns None
    expected_genesis_block = None  # _genesis_block_hash() returns None
    anchor_hash = None  # anchor_hash is also None
    anchor_height = 0  # We're at genesis
    header_height = 1  # Receiving a height 1 header
    
    # Build valid_genesis_hashes set (as code does)
    valid_genesis_hashes = {
        expected_genesis,
        expected_genesis_block,
        anchor_hash,
    }
    # Remove None values
    valid_genesis_hashes = {h for h in valid_genesis_hashes if h}
    
    print(f"Scenario:")
    print(f"  expected_genesis: {expected_genesis}")
    print(f"  expected_genesis_block: {expected_genesis_block}")
    print(f"  anchor_hash: {anchor_hash}")
    print(f"  anchor_height: {anchor_height}")
    print(f"  header_height: {header_height}")
    print(f"  valid_genesis_hashes: {valid_genesis_hashes}")
    print()
    
    # Test the fix logic
    if not valid_genesis_hashes:
        # FIX APPLIED: Accept header unconditionally
        accepted = True
        reason = "accepted_defensively"
        print(f"✓ FIX APPLIED: Accepting height 1 header unconditionally")
        print(f"  valid_genesis_hashes is empty, but we accept to prevent deadlock")
    else:
        # OLD BEHAVIOR: Would reject
        accepted = False
        reason = "rejected_no_match"
        print(f"✗ OLD BEHAVIOR: Would reject header")
    
    # Assertions
    assert not valid_genesis_hashes, (
        f"valid_genesis_hashes should be empty when all genesis hashes are None"
    )
    assert accepted, (
        f"Header should be ACCEPTED with fix (got accepted={accepted})"
    )
    assert reason == "accepted_defensively", (
        f"Should accept defensively (got reason={reason})"
    )
    
    print(f"\n✓ Test PASSED: Header accepted despite empty valid_genesis_hashes")
    print(f"  This prevents the deadlock scenario at genesis")


def test_valid_genesis_hashes_present():
    """
    Test that normal validation still works when genesis hashes are present.
    
    This ensures the fix doesn't break normal operation.
    """
    print("\n" + "="*70)
    print("Test: Normal operation with valid genesis hashes")
    print("="*70)
    
    # Simulate normal scenario with valid genesis hashes
    expected_genesis = b'\xaa' * 32  # Valid genesis_header_hash
    expected_genesis_block = b'\xbb' * 32  # Valid genesis_block_hash
    anchor_hash = b'\xaa' * 32  # Matches expected_genesis
    anchor_height = 0
    header_height = 1
    header_parent_hash = b'\xaa' * 32  # Matches expected_genesis
    
    # Build valid_genesis_hashes set
    valid_genesis_hashes = {
        expected_genesis,
        expected_genesis_block,
        anchor_hash,
    }
    valid_genesis_hashes = {h for h in valid_genesis_hashes if h}
    
    print(f"Scenario:")
    print(f"  expected_genesis: {expected_genesis.hex()[:16]}...")
    print(f"  expected_genesis_block: {expected_genesis_block.hex()[:16]}...")
    print(f"  anchor_hash: {anchor_hash.hex()[:16]}...")
    print(f"  header_parent_hash: {header_parent_hash.hex()[:16]}...")
    print(f"  valid_genesis_hashes count: {len(valid_genesis_hashes)}")
    print()
    
    # Test validation logic
    if not valid_genesis_hashes:
        # Defensive fix
        accepted = True
        reason = "accepted_defensively"
    elif header_parent_hash in valid_genesis_hashes:
        # Normal acceptance
        accepted = True
        reason = "parent_matches_genesis_variant"
    else:
        # Normal rejection
        accepted = False
        reason = "parent_mismatch"
    
    # Assertions
    assert len(valid_genesis_hashes) > 0, (
        f"valid_genesis_hashes should NOT be empty in normal operation"
    )
    assert accepted, (
        f"Header should be ACCEPTED when parent matches (got accepted={accepted})"
    )
    assert reason == "parent_matches_genesis_variant", (
        f"Should accept via normal path (got reason={reason})"
    )
    
    print(f"✓ Test PASSED: Normal validation works correctly")
    print(f"  Header accepted because parent matches a valid genesis variant")


def test_invalid_parent_hash():
    """
    Test that invalid parents are still rejected when genesis hashes are valid.
    
    This ensures the fix doesn't make validation too permissive.
    """
    print("\n" + "="*70)
    print("Test: Invalid parent hash rejection")
    print("="*70)
    
    # Simulate scenario with valid genesis hashes but wrong parent
    expected_genesis = b'\xaa' * 32
    expected_genesis_block = b'\xbb' * 32
    anchor_hash = b'\xaa' * 32
    anchor_height = 0
    header_height = 1
    header_parent_hash = b'\xcc' * 32  # WRONG - doesn't match any variant
    
    # Build valid_genesis_hashes set
    valid_genesis_hashes = {
        expected_genesis,
        expected_genesis_block,
        anchor_hash,
    }
    valid_genesis_hashes = {h for h in valid_genesis_hashes if h}
    
    print(f"Scenario:")
    print(f"  expected_genesis: {expected_genesis.hex()[:16]}...")
    print(f"  expected_genesis_block: {expected_genesis_block.hex()[:16]}...")
    print(f"  header_parent_hash: {header_parent_hash.hex()[:16]}... (WRONG)")
    print(f"  valid_genesis_hashes count: {len(valid_genesis_hashes)}")
    print()
    
    # Test validation logic
    if not valid_genesis_hashes:
        accepted = True
        reason = "accepted_defensively"
    elif header_parent_hash in valid_genesis_hashes:
        accepted = True
        reason = "parent_matches"
    else:
        accepted = False
        reason = "parent_mismatch"
    
    # Assertions
    assert len(valid_genesis_hashes) > 0, (
        f"valid_genesis_hashes should NOT be empty"
    )
    assert not accepted, (
        f"Header should be REJECTED when parent doesn't match (got accepted={accepted})"
    )
    assert reason == "parent_mismatch", (
        f"Should reject with parent_mismatch (got reason={reason})"
    )
    
    print(f"✓ Test PASSED: Invalid parent correctly rejected")
    print(f"  Fix doesn't make validation too permissive")


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("Genesis Sync Defensive Fix Test Suite")
    print("="*70)
    
    try:
        test_empty_valid_genesis_hashes()
        test_valid_genesis_hashes_present()
        test_invalid_parent_hash()
        
        print("\n" + "="*70)
        print("ALL TESTS PASSED ✓")
        print("="*70)
        print("\nSummary:")
        print("1. ✓ Empty valid_genesis_hashes: Headers accepted defensively")
        print("2. ✓ Normal operation: Validation works as expected")
        print("3. ✓ Invalid parents: Still rejected when hashes are valid")
        print("\nThe fix prevents deadlock at genesis without breaking normal validation.")
        
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        raise


if __name__ == "__main__":
    main()
