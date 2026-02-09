#!/usr/bin/env python3
"""
Test: Verify looser validation for mempool admission

Tests that signature field validation gracefully handles various input types
while still maintaining security.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mempool.validate import _safe_to_int, _safe_to_bytes, _extract_sig_tuple, StatelessValidationError


def test_safe_to_int():
    """Test _safe_to_int helper with various input types."""
    print("\n=== Testing _safe_to_int ===")
    
    # Valid cases
    test_cases_valid = [
        (42, 42, "int"),
        ("42", 42, "string"),
        (b"42", 42, "bytes (UTF-8)"),
        (b"\x00\x2a", 42, "bytes (big-endian)"),
    ]
    
    for value, expected, desc in test_cases_valid:
        try:
            result = _safe_to_int(value, "test_field")
            if result == expected:
                print(f"✓ {desc}: {value!r} -> {result}")
            else:
                print(f"❌ {desc}: expected {expected}, got {result}")
                return False
        except Exception as e:
            print(f"❌ {desc}: unexpected error: {e}")
            return False
    
    # Invalid cases (should raise StatelessValidationError)
    test_cases_invalid = [
        (None, "None"),
        ([1, 2, 3], "list"),
        ({"key": "value"}, "dict"),
        ("not a number", "invalid string"),
    ]
    
    for value, desc in test_cases_invalid:
        try:
            result = _safe_to_int(value, "test_field")
            print(f"❌ {desc}: should have raised error, got {result}")
            return False
        except StatelessValidationError as e:
            print(f"✓ {desc}: correctly rejected - {e}")
        except Exception as e:
            print(f"❌ {desc}: wrong error type: {type(e).__name__}: {e}")
            return False
    
    print("✅ _safe_to_int tests passed")
    return True


def test_safe_to_bytes():
    """Test _safe_to_bytes helper with various input types."""
    print("\n=== Testing _safe_to_bytes ===")
    
    # Valid cases
    test_cases_valid = [
        (b"hello", b"hello", "bytes"),
        (bytearray(b"hello"), b"hello", "bytearray"),
        ("0x48656c6c6f", b"Hello", "hex with 0x"),
        ("48656c6c6f", b"Hello", "hex without 0x"),
        ("hello", b"hello", "UTF-8 string"),
        ([72, 101, 108, 108, 111], b"Hello", "list of ints"),
    ]
    
    for value, expected, desc in test_cases_valid:
        try:
            result = _safe_to_bytes(value, "test_field")
            if result == expected:
                print(f"✓ {desc}: {value!r} -> {result!r}")
            else:
                print(f"❌ {desc}: expected {expected!r}, got {result!r}")
                return False
        except Exception as e:
            print(f"❌ {desc}: unexpected error: {e}")
            return False
    
    # Invalid cases
    test_cases_invalid = [
        (None, "None"),
        (b"", "empty bytes"),
        ("", "empty string"),
        ({"key": "value"}, "dict"),
        ([256, 1, 2], "list with out-of-range int"),
        (["a", "b"], "list of strings"),
    ]
    
    for value, desc in test_cases_invalid:
        try:
            result = _safe_to_bytes(value, "test_field")
            print(f"❌ {desc}: should have raised error, got {result!r}")
            return False
        except StatelessValidationError as e:
            print(f"✓ {desc}: correctly rejected - {e}")
        except Exception as e:
            print(f"❌ {desc}: wrong error type: {type(e).__name__}: {e}")
            return False
    
    print("✅ _safe_to_bytes tests passed")
    return True


def test_extract_sig_tuple():
    """Test _extract_sig_tuple with mock Tx objects."""
    print("\n=== Testing _extract_sig_tuple ===")
    
    # Mock Tx class
    class MockTx:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
    
    # Valid transaction
    try:
        tx = MockTx(
            alg_id=4098,  # Dilithium3
            pubkey=b"\x01" * 1952,  # Valid Dilithium3 pubkey size
            signature=b"\x02" * 3309,  # Valid Dilithium3 sig size
        )
        alg_id, pubkey, sig = _extract_sig_tuple(tx)
        if alg_id == 4098 and len(pubkey) == 1952 and len(sig) == 3309:
            print("✓ Valid tx with int alg_id: extracted correctly")
        else:
            print(f"❌ Valid tx: wrong values: alg_id={alg_id}, pubkey_len={len(pubkey)}, sig_len={len(sig)}")
            return False
    except Exception as e:
        print(f"❌ Valid tx: unexpected error: {e}")
        return False
    
    # Transaction with string alg_id (should be converted)
    try:
        tx = MockTx(
            alg_id="4098",  # String that should convert to int
            pubkey=b"\x01" * 1952,
            signature=b"\x02" * 3309,
        )
        alg_id, pubkey, sig = _extract_sig_tuple(tx)
        if alg_id == 4098:
            print("✓ String alg_id: converted to int successfully")
        else:
            print(f"❌ String alg_id: got {alg_id} instead of 4098")
            return False
    except Exception as e:
        print(f"❌ String alg_id: unexpected error: {e}")
        return False
    
    # Transaction with hex string pubkey (should be converted)
    try:
        tx = MockTx(
            alg_id=4098,
            pubkey="0x" + ("01" * 1952),  # Hex string
            signature=b"\x02" * 3309,
        )
        alg_id, pubkey, sig = _extract_sig_tuple(tx)
        if len(pubkey) == 1952:
            print("✓ Hex string pubkey: converted to bytes successfully")
        else:
            print(f"❌ Hex string pubkey: wrong length {len(pubkey)}")
            return False
    except Exception as e:
        print(f"❌ Hex string pubkey: unexpected error: {e}")
        return False
    
    # Transaction with invalid alg_id type (should raise error)
    try:
        tx = MockTx(
            alg_id=[1, 2, 3],  # Invalid type
            pubkey=b"\x01" * 1952,
            signature=b"\x02" * 3309,
        )
        alg_id, pubkey, sig = _extract_sig_tuple(tx)
        print(f"❌ Invalid alg_id type: should have raised error, got {alg_id}")
        return False
    except StatelessValidationError as e:
        print(f"✓ Invalid alg_id type: correctly rejected - {e}")
    except Exception as e:
        print(f"❌ Invalid alg_id type: wrong error: {type(e).__name__}: {e}")
        return False
    
    # Transaction with missing pubkey (should raise error)
    try:
        tx = MockTx(
            alg_id=4098,
            signature=b"\x02" * 3309,
        )
        alg_id, pubkey, sig = _extract_sig_tuple(tx)
        print(f"❌ Missing pubkey: should have raised error")
        return False
    except StatelessValidationError as e:
        print(f"✓ Missing pubkey: correctly rejected - {e}")
    except Exception as e:
        print(f"❌ Missing pubkey: wrong error: {type(e).__name__}: {e}")
        return False
    
    print("✅ _extract_sig_tuple tests passed")
    return True


def main():
    """Run all tests."""
    print("=" * 70)
    print("Testing Looser Validation Fix")
    print("=" * 70)
    
    all_passed = True
    
    if not test_safe_to_int():
        all_passed = False
    
    if not test_safe_to_bytes():
        all_passed = False
    
    if not test_extract_sig_tuple():
        all_passed = False
    
    print("\n" + "=" * 70)
    if all_passed:
        print("✅ ALL TESTS PASSED")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
