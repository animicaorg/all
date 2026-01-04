#!/usr/bin/env python3
"""
Standalone test to verify eth_getBlockByNumber("pending", ...) works correctly.

This test verifies the fix for transaction propagation diagnostic script check #3.
"""

import sys


def test_normalize_block_number():
    """Test that _normalize_block_number handles 'pending' correctly."""
    from rpc.methods.block import _normalize_block_number
    
    # Test pending
    result = _normalize_block_number("pending")
    assert result == "pending", f"Expected 'pending', got {result}"
    
    # Test other keywords still work
    assert _normalize_block_number(0) == 0
    assert _normalize_block_number(10) == 10
    assert _normalize_block_number("0xa") == 10
    assert _normalize_block_number("earliest") == 0
    
    print("✓ _normalize_block_number handles 'pending' correctly")


def test_construct_pending_block():
    """Test that _construct_pending_block returns valid structure."""
    from rpc.methods.block import _construct_pending_block
    
    pending_block = _construct_pending_block()
    
    # Verify required fields are present
    assert "number" in pending_block, "Missing 'number' field"
    assert "hash" in pending_block, "Missing 'hash' field"
    assert "transactions" in pending_block, "Missing 'transactions' field"
    assert "parentHash" in pending_block, "Missing 'parentHash' field"
    
    # Verify pending block has no hash (not mined yet)
    assert pending_block["hash"] is None, "Pending block should have null hash"
    
    # Verify transactions is a list
    assert isinstance(pending_block["transactions"], list), "Transactions should be a list"
    
    print("✓ _construct_pending_block returns valid structure")


def test_chain_get_block_by_number_pending():
    """Test that chain_get_block_by_number handles 'pending' correctly."""
    from rpc.methods.block import chain_get_block_by_number
    
    # Test pending block query
    result = chain_get_block_by_number("pending", False, False)
    
    # Verify it returns a dict
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    
    # Verify required fields
    assert "hash" in result, "Missing 'hash' field"
    assert "transactions" in result, "Missing 'transactions' field"
    
    # Verify pending block has no hash
    assert result["hash"] is None, "Pending block should have null hash"
    
    print("✓ chain_get_block_by_number('pending') works correctly")


def main():
    """Run all tests."""
    try:
        print("\n=== Testing Pending Block Query Support ===\n")
        
        test_normalize_block_number()
        test_construct_pending_block()
        test_chain_get_block_by_number_pending()
        
        print("\n✓ All tests passed!\n")
        return 0
        
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}\n", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}\n", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
