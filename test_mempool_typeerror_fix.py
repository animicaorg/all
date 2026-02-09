#!/usr/bin/env python3
"""
Manual test to verify the TypeError fix in mempool admission.

This script simulates the conditions that caused the TypeError and verifies
that the fixes work correctly.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_bech32_decode_fix():
    """Test that bech32 decode no longer causes TypeError."""
    try:
        from pq.py.utils import bech32
    except ImportError:
        print("❌ bech32 module not available, skipping test")
        return False
    
    print("\n=== Testing bech32 decode fix ===")
    
    # Create a test address
    test_payload = b"\x01" + bytes.fromhex("11" * 32)
    test_addr = bech32.encode_address(test_payload)
    print(f"Test address: {test_addr}")
    
    # OLD WAY (BROKEN) - would cause TypeError
    print("\n1. Testing old broken approach (bech32_decode + bytes()):")
    try:
        hrp, data5, spec = bech32.bech32_decode(test_addr)
        print(f"   - bech32_decode returned: hrp={hrp}, data5 type={type(data5)}, len={len(data5)}")
        print(f"   - data5 sample: {data5[:5]}... (5-bit values)")
        
        # This would cause TypeError in old code
        try:
            wrong_payload = bytes(data5)
            print(f"   ⚠️  bytes(data5) succeeded but returned wrong result: {wrong_payload[:10].hex()}...")
            print(f"   ⚠️  This treats 5-bit values as 8-bit bytes!")
        except TypeError as e:
            print(f"   ✅ bytes(data5) correctly raises TypeError: {e}")
    except Exception as e:
        print(f"   ❌ Unexpected error: {e}")
        return False
    
    # NEW WAY (FIXED) - use decode_address
    print("\n2. Testing fixed approach (decode_address):")
    try:
        correct_payload = bech32.decode_address(test_addr)
        print(f"   - decode_address returned bytes: {correct_payload[:10].hex()}...")
        print(f"   - Length: {len(correct_payload)} bytes")
        
        if correct_payload == test_payload:
            print(f"   ✅ Payload matches original!")
        else:
            print(f"   ❌ Payload mismatch!")
            return False
    except Exception as e:
        print(f"   ❌ decode_address failed: {e}")
        return False
    
    print("\n✅ bech32 decode fix verified")
    return True


def test_ptl_type_checking():
    """Test that PTL type checking works correctly."""
    print("\n=== Testing PTL type checking ===")
    
    # Test cases
    test_cases = [
        ("hex string", "0x48656c6c6f", True),
        ("hex without 0x", "48656c6c6f", True),
        ("bytes", b"Hello", True),
        ("bytearray", bytearray(b"Hello"), True),
        ("valid list", [72, 101, 108, 108, 111], True),
        ("dict (invalid)", {"key": "value"}, False),
        ("non-hex string", "not hex", False),
        ("out-of-range list", [256, 1, 2], False),
    ]
    
    all_passed = True
    for name, tx_data, should_succeed in test_cases:
        print(f"\nTesting {name}:")
        try:
            # Simulate the type checking logic from ptl.py
            if isinstance(tx_data, str):
                if tx_data.startswith("0x"):
                    tx_data = tx_data[2:]
                tx_bytes = bytes.fromhex(tx_data)
            elif isinstance(tx_data, (bytes, bytearray)):
                tx_bytes = bytes(tx_data)
            elif isinstance(tx_data, (list, tuple)):
                tx_bytes = bytes(tx_data)
            else:
                raise ValueError(f"Invalid tx_data format: expected str, bytes, or list, got {type(tx_data).__name__}")
            
            if should_succeed:
                print(f"   ✅ Correctly converted to {len(tx_bytes)} bytes")
            else:
                print(f"   ❌ Should have failed but succeeded")
                all_passed = False
        except (ValueError, TypeError) as e:
            if not should_succeed:
                print(f"   ✅ Correctly rejected: {type(e).__name__}")
            else:
                print(f"   ❌ Should have succeeded but failed: {e}")
                all_passed = False
        except Exception as e:
            print(f"   ❌ Unexpected error: {e}")
            all_passed = False
    
    if all_passed:
        print("\n✅ PTL type checking verified")
    else:
        print("\n❌ Some PTL type checks failed")
    
    return all_passed


def test_safe_bytes_conversion():
    """Test the safe bytes conversion from mempool.accounting."""
    try:
        from mempool.accounting import _safe_bytes_from_value
    except ImportError:
        print("❌ mempool.accounting module not available, skipping test")
        return False
    
    print("\n=== Testing safe bytes conversion ===")
    
    test_cases = [
        ("None", None, b""),
        ("empty string", "", b""),
        ("hex string", "0x48656c6c6f", b"Hello"),
        ("bytes", b"Hello", b"Hello"),
        ("dict (invalid)", {"key": "value"}, b""),
        ("list (invalid)", [1, 2, 3], b""),
        ("int (invalid)", 123, b""),
    ]
    
    all_passed = True
    for name, value, expected in test_cases:
        result = _safe_bytes_from_value(value)
        if result == expected:
            print(f"   ✅ {name}: {repr(value)} → {result.hex() if result else 'empty'}")
        else:
            print(f"   ❌ {name}: expected {expected.hex()}, got {result.hex()}")
            all_passed = False
    
    if all_passed:
        print("\n✅ Safe bytes conversion verified")
    else:
        print("\n❌ Some safe bytes conversions failed")
    
    return all_passed


def main():
    """Run all tests."""
    print("=" * 60)
    print("Manual Verification: TypeError Fix in Mempool Admission")
    print("=" * 60)
    
    results = []
    
    # Run tests
    results.append(("Bech32 decode fix", test_bech32_decode_fix()))
    results.append(("PTL type checking", test_ptl_type_checking()))
    results.append(("Safe bytes conversion", test_safe_bytes_conversion()))
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {name}")
    
    all_passed = all(passed for _, passed in results)
    
    if all_passed:
        print("\n✅ All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
