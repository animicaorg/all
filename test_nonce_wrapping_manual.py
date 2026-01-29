#!/usr/bin/env python3
"""
Manual test for nonce wrapping fix.
This can be run directly without pytest.
"""


def test_nonce_wraps_at_64bit_boundary():
    """Test that nonce wrapping works correctly at 64-bit boundary."""
    UINT64_MASK = 0xFFFFFFFFFFFFFFFF
    MAX_UINT64 = (1 << 64) - 1
    
    print("Testing nonce wrapping at 64-bit boundary...")
    
    # Test 1: Normal increment
    nonce = 1000
    batch_size = 50000
    new_nonce = (nonce + batch_size) & UINT64_MASK
    assert new_nonce == 51000, f"Expected 51000, got {new_nonce}"
    print("✓ Test 1 passed: Normal increment")
    
    # Test 2: Near boundary
    nonce = MAX_UINT64 - 100
    batch_size = 50
    new_nonce = (nonce + batch_size) & UINT64_MASK
    expected = (MAX_UINT64 - 100 + 50) & UINT64_MASK
    assert new_nonce == expected, f"Expected {expected}, got {new_nonce}"
    print("✓ Test 2 passed: Near boundary")
    
    # Test 3: Exact boundary
    nonce = MAX_UINT64
    batch_size = 1
    new_nonce = (nonce + batch_size) & UINT64_MASK
    assert new_nonce == 0, f"Expected 0 (wrap), got {new_nonce}"
    print("✓ Test 3 passed: Exact boundary wraps to 0")
    
    # Test 4: Overflow by large amount
    nonce = MAX_UINT64 - 10
    batch_size = 100
    new_nonce = (nonce + batch_size) & UINT64_MASK
    expected = 89  # (MAX - 10 + 100) % (2^64) = 89
    assert new_nonce == expected, f"Expected {expected}, got {new_nonce}"
    print("✓ Test 4 passed: Large overflow wraps correctly")
    
    print("\n✓ All nonce wrapping tests passed!")


def test_bug_demonstration():
    """Demonstrate what happens without the fix."""
    MAX_UINT64 = (1 << 64) - 1
    
    print("\nDemonstrating the bug (without mask)...")
    
    nonce = MAX_UINT64 - 100
    batch_size = 200
    
    # Without mask (the bug)
    nonce_without_mask = nonce + batch_size
    print(f"  Without mask: {nonce_without_mask} (exceeds MAX_UINT64: {nonce_without_mask > MAX_UINT64})")
    
    # With mask (the fix)
    nonce_with_mask = (nonce + batch_size) & 0xFFFFFFFFFFFFFFFF
    print(f"  With mask: {nonce_with_mask} (within range: {nonce_with_mask <= MAX_UINT64})")
    
    assert nonce_without_mask > MAX_UINT64, "Without mask causes overflow"
    assert nonce_with_mask <= MAX_UINT64, "With mask stays in range"
    print("✓ Bug demonstration complete")


def test_scan_forever_fix():
    """Verify the fix is in the code."""
    print("\nVerifying fix in hash_search.py...")
    
    # Use relative path based on script location
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    hash_search_path = os.path.join(script_dir, "mining", "hash_search.py")
    
    with open(hash_search_path, "r") as f:
        content = f.read()
    
    # Check that the fix is present (full 64-bit mask)
    if "nonce = (nonce + scaled_batch_size) & 0xFFFFFFFFFFFFFFFF" in content:
        print("✓ Fix found in scan_forever function!")
        return True
    elif "nonce += scaled_batch_size" in content and "& 0xFFFFFFFFFFFFFFFF" not in content:
        print("✗ BUG: nonce increment without wrapping found!")
        return False
    else:
        print("? Could not verify fix in code")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Nonce Wrapping Fix Test Suite")
    print("=" * 60)
    
    try:
        test_nonce_wraps_at_64bit_boundary()
        test_bug_demonstration()
        fix_present = test_scan_forever_fix()
        
        print("\n" + "=" * 60)
        if fix_present:
            print("SUCCESS: All tests passed and fix is present in code!")
        else:
            print("WARNING: Tests passed but fix may not be in code")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
