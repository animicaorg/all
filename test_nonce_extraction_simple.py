#!/usr/bin/env python3
"""
Simple test to verify nonce extraction logic without dependencies.
"""

def _coerce_int(value):
    """Copied from tx.py"""
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith(("0x", "0X")):
            try:
                return int(text, 16)
            except ValueError:
                return None
        if text.isdigit():
            return int(text)
    return None


def _extract_nonce_mismatch(data, *, verbose=False):
    """Copied from tx.py with enhancements"""
    if not isinstance(data, dict):
        if verbose:
            print(f"  data is not dict, got {type(data).__name__}")
        return None, None, None
    
    reason = None
    expected = None
    got = None
    
    # Try wrapped mempoolError first (RPC layer wraps mempool errors)
    mempool_error = data.get("mempoolError")
    if isinstance(mempool_error, dict):
        reason = mempool_error.get("reason")
        context = mempool_error.get("context")
        if isinstance(context, dict):
            expected = context.get("expected_nonce") or context.get("expected")
            got = context.get("got_nonce") or context.get("got")
        if verbose:
            print(f"  from mempoolError: reason={reason}, expected={expected}, got={got}")
    else:
        # Try direct context (mempool.getStatus or older error formats)
        reason = data.get("reason")
        expected = data.get("expected") or data.get("expected_nonce") or data.get("highest")
        got = data.get("got") or data.get("got_nonce")
        if verbose:
            print(f"  from direct context: reason={reason}, expected={expected}, got={got}")
    
    expected = _coerce_int(expected)
    got = _coerce_int(got)
    
    return reason, expected, got


def test_extraction_from_mempool_error():
    """Test extraction from mempoolError wrapper (RPC layer format)."""
    print("\n1. Testing extraction from mempoolError wrapper...")
    
    error_data = {
        "mempoolError": {
            "code": 1005,
            "reason": "nonce_too_low",
            "message": "nonce too low: expected 10, got 8",
            "context": {
                "sender": "0x1234",
                "tx_hash": "0xabcd",
                "expected_nonce": 10,
                "got_nonce": 8,
            },
        }
    }
    
    reason, expected, got = _extract_nonce_mismatch(error_data, verbose=True)
    
    assert reason == "nonce_too_low", f"Expected reason='nonce_too_low', got '{reason}'"
    assert expected == 10, f"Expected expected=10, got {expected}"
    assert got == 8, f"Expected got=8, got {got}"
    
    print("  ✓ Correctly extracted: reason='nonce_too_low', expected=10, got=8")


def test_extraction_from_direct_context():
    """Test extraction from direct context (mempool.getStatus format)."""
    print("\n2. Testing extraction from direct context...")
    
    error_data = {
        "reason": "nonce_gap",
        "expected": 15,
        "got": 20,
    }
    
    reason, expected, got = _extract_nonce_mismatch(error_data, verbose=True)
    
    assert reason == "nonce_gap", f"Expected reason='nonce_gap', got '{reason}'"
    assert expected == 15, f"Expected expected=15, got {expected}"
    assert got == 20, f"Expected got=20, got {got}"
    
    print("  ✓ Correctly extracted: reason='nonce_gap', expected=15, got=20")


def test_extraction_with_alt_field_names():
    """Test extraction with alternative field names."""
    print("\n3. Testing extraction with alternative field names...")
    
    # Test with "highest" instead of "expected"
    error_data = {
        "reason": "nonce_gap",
        "highest": 12,
        "got_nonce": 15,
    }
    
    reason, expected, got = _extract_nonce_mismatch(error_data, verbose=True)
    
    assert reason == "nonce_gap"
    assert expected == 12, f"Expected expected=12 (from 'highest'), got {expected}"
    assert got == 15
    
    print("  ✓ Correctly extracted: reason='nonce_gap', expected=12 (from 'highest'), got=15")


def test_extraction_nonce_gap_with_pending_next():
    """Test that nonce_gap with pending_next in mempoolError is handled."""
    print("\n4. Testing nonce_gap with pending_next in mempoolError...")
    
    error_data = {
        "mempoolError": {
            "code": 1002,
            "reason": "nonce_gap",
            "message": "nonce gap: expected 15, got 20",
            "context": {
                "sender": "0x1234",
                "tx_hash": "0xabcd",
                "expected_nonce": 15,  # This is pending_next (the nonce to retry with)
                "got_nonce": 20,
            },
        }
    }
    
    reason, expected, got = _extract_nonce_mismatch(error_data, verbose=True)
    
    assert reason == "nonce_gap"
    assert expected == 15, f"Expected expected=15 (pending_next), got {expected}"
    assert got == 20
    
    print("  ✓ Correctly extracted: reason='nonce_gap', expected=15 (pending_next), got=20")


def main():
    print("=" * 70)
    print("Testing Nonce Extraction Logic")
    print("=" * 70)
    
    try:
        test_extraction_from_mempool_error()
        test_extraction_from_direct_context()
        test_extraction_with_alt_field_names()
        test_extraction_nonce_gap_with_pending_next()
        
        print("\n" + "=" * 70)
        print("✅ ALL TESTS PASSED!")
        print("=" * 70)
        print("\nThe CLI nonce extraction logic correctly handles:")
        print("  • mempoolError wrapper from RPC layer")
        print("  • Direct context from mempool.getStatus")
        print("  • Alternative field names (highest, expected_nonce, got_nonce)")
        print("  • Nonce gap with pending_next as expected_nonce")
        return 0
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
