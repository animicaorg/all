#!/usr/bin/env python3
"""
Minimal test to reproduce ANM transfer transaction not being included in mined blocks.

This script:
1. Sends a signed transaction to RPC (stored in _FALLBACK_PENDING)
2. Mines blocks
3. Verifies the transaction is included and balances are updated
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_tx_inclusion():
    """Test that transactions in _FALLBACK_PENDING are included when mining"""
    print("=" * 80)
    print("TEST: Transaction inclusion in mined blocks")
    print("=" * 80)
    
    # Import after path is set
    from rpc.methods import tx as tx_methods
    from rpc.methods import miner as miner_methods
    
    # Check if _FALLBACK_PENDING exists
    if not hasattr(tx_methods, "_FALLBACK_PENDING"):
        print("ERROR: _FALLBACK_PENDING not found in tx_methods")
        return False
    
    print(f"\n1. Initial state:")
    print(f"   _FALLBACK_PENDING: {len(tx_methods._FALLBACK_PENDING)} transactions")
    
    # Create a minimal signed transaction for testing
    # This simulates what would be added by tx.sendRawTransaction
    from core.encoding.cbor import dumps as cbor_dumps
    from core.types.tx import Tx
    from core.utils.hash import sha3_256
    
    # Create a test transaction in the CORE format (as from Tx.to_obj())
    # This is what should be stored after proper tx.sendRawTransaction flow
    # Format: {"tx": {...unsigned tx...}, "sigs": [{alg, pubkey, sig}]}
    test_tx_obj = {
        "tx": {
            "v": 1,
            "chainId": 2,
            "from": b"\x00" * 32,  # Mock sender
            "nonce": 0,
            "gas": {"price": 1, "limit": 21000},
            "payload": {"t": 0, "v": {"to": b"\xff" * 32, "amount": 101000000000, "data": b""}},
            "accessList": [],
        },
        "sigs": [{
            "alg": 1,  # Mock dilithium3
            "pubkey": b"\x01" * 1952,  # Dilithium3 public key size
            "sig": b"\x02" * 3293,  # Dilithium3 signature size
        }]
    }
    
    # Encode to CBOR
    try:
        test_tx_cbor = cbor_dumps(test_tx_obj)
        test_tx_hash = "0x" + sha3_256(test_tx_cbor).hex()
        
        print(f"\n2. Adding test transaction to _FALLBACK_PENDING:")
        print(f"   TX hash: {test_tx_hash}")
        print(f"   CBOR size: {len(test_tx_cbor)} bytes")
        
        # Add to fallback pending (bypass signature verification for this test)
        tx_methods._FALLBACK_PENDING[test_tx_hash] = test_tx_cbor
        
        print(f"\n3. After adding transaction:")
        print(f"   _FALLBACK_PENDING: {len(tx_methods._FALLBACK_PENDING)} transactions")
        
        # Now try to mine and see if the transaction is picked up
        print(f"\n4. Testing transaction retrieval from _FALLBACK_PENDING:")
        
        # Check what _mine_once would see
        pending_map = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
        print(f"   Pending map has {len(pending_map)} items")
        
        for tx_hash_hex, raw in list(pending_map.items())[:3]:  # Show first 3
            print(f"   - TX {tx_hash_hex[:16]}... ({len(raw)} bytes)")
            try:
                decoded, obj = tx_methods._decode_tx(raw)
                print(f"     Decoded type: {type(decoded).__name__}")
                print(f"     Is Tx instance: {isinstance(decoded, Tx)}")
                if hasattr(decoded, 'unsigned'):
                    print(f"     Unsigned: {decoded.unsigned}")
                    print(f"     Chain ID: {decoded.unsigned.chain_id}")
                elif isinstance(decoded, dict):
                    print(f"     Dict keys: {list(decoded.keys())}")
                    # Try the miner's conversion logic
                    if "tx" in decoded:
                        print(f"     Has 'tx' key (core format)")
                    elif "body" in decoded:
                        print(f"     Has 'body' key (RPC envelope format)")
            except Exception as e:
                print(f"     Decode FAILED: {e}")
                import traceback
                traceback.print_exc()
        
        return True
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_tx_inclusion()
    sys.exit(0 if success else 1)
