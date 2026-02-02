"""
Test that mining uses randomized nonce starting points to prevent stall after 20 blocks.

This test verifies the fix where nonce starts from a random value rather than
sequentially growing, making mining time-based and hash-power dependent.
"""
import sys
import os

# Add paths for imports
sys.path.insert(0, '/home/runner/work/all/all')

def test_mine_header_code_inspection():
    """Test that _mine_header code uses secrets.randbelow for nonce initialization."""
    import inspect
    
    # Read the mining.py file directly to check the implementation
    mining_file = '/home/runner/work/all/all/python/animica/cli/mining.py'
    
    with open(mining_file, 'r') as f:
        content = f.read()
    
    # Check for randomized nonce initialization
    assert 'secrets.randbelow(2**32)' in content, "Should use secrets.randbelow for nonce"
    assert 'start_nonce = secrets.randbelow(2**32)' in content, "Should initialize start_nonce randomly"
    assert '& 0xFFFFFFFFFFFFFFFF' in content, "Should wrap nonce at 64-bit boundary"
    
    print("✓ _mine_header uses randomized starting nonce (code inspection)")
    print("  - Uses secrets.randbelow(2**32) for initialization")
    print("  - Wraps nonce at 64-bit boundary to prevent overflow")


def test_hash_search_scan_forever_code_inspection():
    """Test that scan_forever uses random nonce for new templates."""
    
    # Read the hash_search.py file directly to check the implementation
    hash_search_file = '/home/runner/work/all/all/mining/hash_search.py'
    
    with open(hash_search_file, 'r') as f:
        content = f.read()
    
    # Check for randomized nonce initialization in scan_forever
    assert 'secrets.randbelow(2**32)' in content, "Should use secrets.randbelow for nonce"
    assert 'import secrets' in content, "Should import secrets module"
    
    # Count occurrences to ensure it's used in multiple places
    nonce_init_count = content.count('nonce = secrets.randbelow(2**32)')
    assert nonce_init_count >= 2, f"Should use random nonce in at least 2 places (initial + template change), found {nonce_init_count}"
    
    print("✓ scan_forever uses randomized nonce for new templates (code inspection)")
    print(f"  - Found {nonce_init_count} occurrences of random nonce initialization")
    print("  - Nonce is reset to random value on new template/job")


def test_nonce_wrapping_implementation():
    """
    Test that nonce wrapping is implemented correctly to prevent overflow.
    """
    
    # Read the mining.py file directly to check the implementation
    mining_file = '/home/runner/work/all/all/python/animica/cli/mining.py'
    
    with open(mining_file, 'r') as f:
        content = f.read()
    
    # Check for proper nonce wrapping in retry loop
    assert '(start_nonce + max_nonce) & 0xFFFFFFFFFFFFFFFF' in content, \
        "Should wrap nonce at 64-bit boundary in retry loop"
    
    # Check that old unbounded increment is gone
    assert content.count('start_nonce += max_nonce') == 0, \
        "Old unbounded nonce increment should be replaced with wrapped version"
    
    print("✓ Nonce wrapping prevents overflow (code inspection)")
    print("  - Uses & 0xFFFFFFFFFFFFFFFF to wrap at 64-bit boundary")
    print("  - Prevents unbounded nonce growth across retry windows")


if __name__ == "__main__":
    print("=" * 70)
    print("Nonce Randomization Test Suite")
    print("=" * 70)
    
    try:
        print("\n[1/3] Testing _mine_header randomized nonce...")
        test_mine_header_code_inspection()
        
        print("\n[2/3] Testing scan_forever randomized nonce...")
        test_hash_search_scan_forever_code_inspection()
        
        print("\n[3/3] Testing nonce wrapping implementation...")
        test_nonce_wrapping_implementation()
        
        print("\n" + "=" * 70)
        print("SUCCESS: All nonce randomization tests passed!")
        print("Mining will now use random starting nonces, making it:")
        print("  - Time-based rather than sequential")
        print("  - More about hash power than nonce progression")
        print("  - Less likely to stall after 20+ blocks")
        print("=" * 70)
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
