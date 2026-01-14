#!/usr/bin/env python3
"""
Verification script for bech32 address encoding fix.

This script verifies that the address encoding is consistent across:
- Python address generation
- Python address decoding
- RPC address reconstruction

Run this after applying the fix to ensure everything works correctly.
"""

import sys
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root))

from pq.py.address import address_from_pubkey, decode_address, AddressRecord
from pq.py.utils.hash import sha3_256


def test_address_generation():
    """Test that addresses are generated with correct format."""
    print("=" * 60)
    print("TEST 1: Address Generation")
    print("=" * 60)
    
    # Use known test pubkey
    pubkey = b'\x01' * 48
    alg_id = 0x1001  # dilithium3 from pq/alg_ids.yaml
    
    # Generate address
    addr = address_from_pubkey(pubkey, alg_id)
    print(f"✓ Generated address: {addr}")
    
    # Decode it
    rec = decode_address(addr)
    
    # Verify algorithm ID
    assert rec.alg_id == 0x1001, f"Expected alg_id 0x1001, got 0x{rec.alg_id:04x}"
    print(f"✓ Algorithm ID: 0x{rec.alg_id:04x} (correct)")
    
    # Verify digest length
    digest_bytes = bytes(rec.digest) if not isinstance(rec.digest, bytes) else rec.digest
    assert len(digest_bytes) == 32, f"Expected digest length 32, got {len(digest_bytes)}"
    print(f"✓ Digest length: {len(digest_bytes)} bytes (correct)")
    
    # Verify digest matches
    expected_digest = sha3_256(pubkey)
    assert digest_bytes == expected_digest, "Digest mismatch!"
    print(f"✓ Digest matches: {digest_bytes.hex()[:32]}...")
    
    print("\n✅ Address generation test passed!\n")
    return addr, alg_id, digest_bytes


def test_address_reconstruction():
    """Test that addresses can be reconstructed from digest."""
    print("=" * 60)
    print("TEST 2: Address Reconstruction (RPC simulation)")
    print("=" * 60)
    
    # Simulate what RPC does: reconstruct address from stored digest
    _, alg_id, digest = test_address_generation()
    
    # Reconstruct address (like RPC getRichList does)
    addr_rec = AddressRecord(hrp="anim", alg_id=alg_id, digest=digest)
    reconstructed_addr = addr_rec.to_string()
    print(f"✓ Reconstructed address: {reconstructed_addr}")
    
    # Verify it can be decoded
    rec = decode_address(reconstructed_addr)
    assert rec.alg_id == alg_id, f"Algorithm ID mismatch after reconstruction"
    print(f"✓ Algorithm ID after reconstruction: 0x{rec.alg_id:04x} (correct)")
    
    rec_digest = bytes(rec.digest) if not isinstance(rec.digest, bytes) else rec.digest
    assert rec_digest == digest, "Digest mismatch after reconstruction!"
    print(f"✓ Digest matches after reconstruction")
    
    print("\n✅ Address reconstruction test passed!\n")


def test_multiple_algorithms():
    """Test that both dilithium3 and sphincs work correctly."""
    print("=" * 60)
    print("TEST 3: Multiple Algorithm Support")
    print("=" * 60)
    
    pubkey = b'\x02' * 64  # Different test key
    
    algorithms = [
        (0x1001, "dilithium3"),
        (0x1002, "sphincs_shake_128s"),
    ]
    
    for alg_id, name in algorithms:
        addr = address_from_pubkey(pubkey, alg_id)
        rec = decode_address(addr)
        
        assert rec.alg_id == alg_id, f"Algorithm ID mismatch for {name}"
        print(f"✓ {name} (0x{alg_id:04x}): {addr[:30]}...")
    
    print("\n✅ Multiple algorithm test passed!\n")


def test_payload_format():
    """Test that payload has correct structure."""
    print("=" * 60)
    print("TEST 4: Payload Format Verification")
    print("=" * 60)
    
    pubkey = b'\x03' * 48
    alg_id = 0x1001
    
    # Generate address
    addr = address_from_pubkey(pubkey, alg_id)
    
    # Decode to get raw payload
    from pq.py.utils import bech32 as _b32
    _, data5, spec = _b32.bech32_decode(addr)
    
    assert spec == "bech32m", f"Expected bech32m, got {spec}"
    print(f"✓ Encoding: {spec}")
    
    # Convert 5-bit to 8-bit
    payload = _b32.convertbits(data5, 5, 8, False)
    
    # Verify payload length
    assert len(payload) == 34, f"Expected payload length 34, got {len(payload)}"
    print(f"✓ Payload length: {len(payload)} bytes (2-byte alg_id + 32-byte digest)")
    
    # Verify algorithm ID in payload
    payload_alg_id = int.from_bytes(payload[0:2], "big")
    assert payload_alg_id == alg_id, f"Algorithm ID mismatch in payload"
    print(f"✓ Payload alg_id: 0x{payload_alg_id:04x} (2-byte big-endian)")
    
    # Verify digest in payload
    payload_digest = bytes(payload[2:])
    expected_digest = sha3_256(pubkey)
    assert payload_digest == expected_digest, f"Payload digest mismatch: {payload_digest.hex()[:32]} != {expected_digest.hex()[:32]}"
    print(f"✓ Payload digest: {payload_digest.hex()[:32]}...")
    
    print("\n✅ Payload format test passed!\n")


def main():
    """Run all verification tests."""
    print("\n" + "=" * 60)
    print("BECH32 ADDRESS ENCODING FIX VERIFICATION")
    print("=" * 60 + "\n")
    
    try:
        test_address_generation()
        test_address_reconstruction()
        test_multiple_algorithms()
        test_payload_format()
        
        print("=" * 60)
        print("✅ ALL VERIFICATION TESTS PASSED!")
        print("=" * 60)
        print("\nThe bech32 address encoding fix is working correctly.")
        print("Addresses now use 2-byte algorithm IDs as per pq/alg_ids.yaml")
        print("\nNext steps:")
        print("  1. Test with actual wallet and explorer")
        print("  2. Verify balance lookups work correctly")
        print("  3. Check that old addresses still resolve (by digest)")
        return 0
        
    except AssertionError as e:
        print("\n" + "=" * 60)
        print("❌ VERIFICATION FAILED!")
        print("=" * 60)
        print(f"\nError: {e}")
        print("\nPlease check the fix implementation.")
        return 1
    except Exception as e:
        print("\n" + "=" * 60)
        print("❌ UNEXPECTED ERROR!")
        print("=" * 60)
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
