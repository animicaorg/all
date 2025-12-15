#!/usr/bin/env python3
"""
Test that CLI tx.send now uses 32-byte digest addresses.

This script validates that the fix converts bech32 addresses to 32-byte digests
before building the transaction body, ensuring consistent address canonicalization.
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(__file__))


def test_cli_address_conversion():
    """Test that _address_to_32_bytes correctly converts bech32 to 32-byte digest."""
    from python.animica.cli.tx import _address_to_32_bytes
    from pq.py.keygen import keygen_sig
    from pq.py.address import decode_address
    
    print("\n" + "="*70)
    print("TEST: CLI address conversion to 32-byte digest")
    print("="*70)
    
    # Generate a test keypair
    kp = keygen_sig("dilithium3")
    bech32_addr = kp.address
    
    print(f"\n1. Generate test address")
    print(f"   Bech32: {bech32_addr}")
    
    # Decode using pq.py.address to get expected digest
    addr_record = decode_address(bech32_addr)
    expected_digest = bytes(addr_record.digest) if isinstance(addr_record.digest, list) else addr_record.digest
    expected_digest = expected_digest[:32].ljust(32, b"\x00")
    
    print(f"   Expected digest (32 bytes): {expected_digest.hex()[:16]}...")
    
    # Test the CLI helper
    cli_digest = _address_to_32_bytes(bech32_addr)
    
    print(f"   CLI digest (32 bytes):      {cli_digest.hex()[:16]}...")
    
    # Verify they match
    if cli_digest == expected_digest:
        print(f"\n   ✓ PASS: CLI helper produces correct 32-byte digest")
        return True
    else:
        print(f"\n   ✗ FAIL: Digest mismatch!")
        print(f"   Expected: {expected_digest.hex()}")
        print(f"   Got:      {cli_digest.hex()}")
        return False


def test_tx_body_uses_bytes():
    """Test that _build_tx_body returns bytes for from/to fields."""
    from python.animica.cli.tx import _build_tx_body
    from pq.py.keygen import keygen_sig
    
    print("\n" + "="*70)
    print("TEST: TX body uses bytes for addresses")
    print("="*70)
    
    # Generate test keypairs
    sender_kp = keygen_sig("dilithium3")
    recipient_kp = keygen_sig("dilithium3")
    
    sender_bech32 = sender_kp.address
    recipient_bech32 = recipient_kp.address
    
    print(f"\n1. Build tx body with bech32 addresses")
    print(f"   From: {sender_bech32}")
    print(f"   To:   {recipient_bech32}")
    
    # Build tx body
    body = _build_tx_body(
        chain_id=1,
        from_addr=sender_bech32,
        to_addr=recipient_bech32,
        nonce=0,
        value_base_units=1_000_000_000,
        gas_limit=21000,
        max_fee=1,
        data=b"",
    )
    
    print(f"\n2. Check body field types")
    print(f"   from type: {type(body['from']).__name__}")
    print(f"   to type:   {type(body['to']).__name__}")
    
    # Verify they are bytes
    if isinstance(body["from"], bytes) and isinstance(body["to"], bytes):
        print(f"\n   ✓ PASS: Both from/to are bytes")
        
        # Check length
        if len(body["from"]) == 32 and len(body["to"]) == 32:
            print(f"   ✓ PASS: Both addresses are 32 bytes")
            print(f"   from: {body['from'].hex()[:16]}...")
            print(f"   to:   {body['to'].hex()[:16]}...")
            return True
        else:
            print(f"   ✗ FAIL: Address length mismatch (from={len(body['from'])}, to={len(body['to'])})")
            return False
    else:
        print(f"   ✗ FAIL: Addresses are not bytes")
        print(f"   from: {body['from']}")
        print(f"   to:   {body['to']}")
        return False


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("CLI TX ADDRESS FIX VERIFICATION")
    print("="*70)
    
    results = []
    
    try:
        results.append(("CLI address conversion", test_cli_address_conversion()))
    except Exception as e:
        print(f"\n   ✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        results.append(("CLI address conversion", False))
    
    try:
        results.append(("TX body uses bytes", test_tx_body_uses_bytes()))
    except Exception as e:
        print(f"\n   ✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        results.append(("TX body uses bytes", False))
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status} {name}")
    
    all_passed = all(passed for _, passed in results)
    
    print("\n" + "="*70)
    print(f"OVERALL: {'✓ ALL TESTS PASSED' if all_passed else '✗ SOME TESTS FAILED'}")
    print("="*70)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
