#!/usr/bin/env python3
"""
Verify that TypeScript extension generates same addresses as Python CLI

This script generates test vectors that can be used to verify the extension
generates addresses identical to the Python node/CLI implementation.
"""

import json
import sys
from pq.py.address import address_from_pubkey
from pq.py.registry import ALG_ID

def generate_test_vectors():
    """Generate test vectors for address encoding verification"""
    
    vectors = []
    
    # Test vector 1: Dilithium3 with repeating 0x01 bytes (short pubkey for demo)
    pubkey1 = bytes.fromhex('01' * 32)
    alg_id1 = ALG_ID.get('dilithium3', 0x1001)
    addr1 = address_from_pubkey(pubkey1, alg_id1)
    
    vectors.append({
        "name": "Dilithium3 with 0x01 bytes",
        "pubkey_hex": pubkey1.hex(),
        "alg_id": alg_id1,
        "alg_name": "dilithium3",
        "address": addr1
    })
    
    # Test vector 2: Dilithium3 with repeating 0xff bytes
    pubkey2 = bytes.fromhex('ff' * 32)
    addr2 = address_from_pubkey(pubkey2, alg_id1)
    
    vectors.append({
        "name": "Dilithium3 with 0xff bytes",
        "pubkey_hex": pubkey2.hex(),
        "alg_id": alg_id1,
        "alg_name": "dilithium3",
        "address": addr2
    })
    
    # Test vector 3: Dilithium3 with random-looking bytes
    pubkey3 = bytes.fromhex('a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2')
    addr3 = address_from_pubkey(pubkey3, alg_id1)
    
    vectors.append({
        "name": "Dilithium3 with mixed bytes",
        "pubkey_hex": pubkey3.hex(),
        "alg_id": alg_id1,
        "alg_name": "dilithium3",
        "address": addr3
    })
    
    # Test vector 4: SPHINCS+ (different algorithm)
    alg_id2 = ALG_ID.get('sphincs_shake_128s', 0x1002)
    addr4 = address_from_pubkey(pubkey3, alg_id2)
    
    vectors.append({
        "name": "SPHINCS+ with mixed bytes",
        "pubkey_hex": pubkey3.hex(),
        "alg_id": alg_id2,
        "alg_name": "sphincs_shake_128s",
        "address": addr4
    })
    
    return vectors

if __name__ == "__main__":
    vectors = generate_test_vectors()
    
    print("# Address Encoding Test Vectors")
    print()
    print("These test vectors verify that the TypeScript extension generates")
    print("addresses identical to the Python node/CLI implementation.")
    print()
    
    for i, vec in enumerate(vectors, 1):
        print(f"## Test Vector {i}: {vec['name']}")
        print(f"- **Public Key (hex)**: `{vec['pubkey_hex']}`")
        print(f"- **Algorithm ID**: `0x{vec['alg_id']:04x}` ({vec['alg_name']})")
        print(f"- **Expected Address**: `{vec['address']}`")
        print()
    
    # Also output as JSON for easy import into tests
    print("## JSON Format")
    print("```json")
    print(json.dumps(vectors, indent=2))
    print("```")
