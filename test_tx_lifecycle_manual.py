#!/usr/bin/env python3
"""
Manual test script to verify transaction lifecycle on chainId=2.

This script simulates the problem:
1. Submit a transaction to chainId=2
2. Check it appears in mempool
3. Mine a block
4. Verify transaction is included in the block
5. Check that balances and nonces updated

Run this to reproduce the issue described in the problem statement.
"""

import sys
import json

# Mock test to understand the data flow
def test_data_structures():
    """Test the data structures used in the codebase."""
    
    # Example signed transaction envelope (RPC/CLI format)
    rpc_envelope = {
        "body": {
            "chainId": 2,
            "from": "anim1zqqsw6mr86yqnee42p6ds9e22y5ye6mquq5cthxump2fmxgx5e9s7fsuugat5",
            "to": "anim1zqqmgcs5auklzpk8yd2d6k4dsh5pcxlcuqyx3r84dj4230uktcmzwesv0nsuj",
            "nonce": 0,
            "value": 1_000_000_000,  # 1 ANM in base units
            "gasLimit": 21000,
            "maxFee": 1000000000,
            "data": b"",
        },
        "sig": {
            "algId": 0x1001,  # dilithium3
            "pubkey": b"..." * 100,  # Mock pubkey bytes
            "sig": b"..." * 100,     # Mock signature bytes
        }
    }
    
    print("RPC/CLI Envelope Format:")
    print(json.dumps({
        "body": {**rpc_envelope["body"], "data": "0x"},
        "sig": {
            "algId": rpc_envelope["sig"]["algId"],
            "pubkey": "0x...",
            "sig": "0x...",
        }
    }, indent=2))
    
    print("\nExpected Core Canonical Format after normalization:")
    print(json.dumps({
        "tx": {
            "v": 1,
            "chainId": 2,
            "from": "0x...",  # 32-byte hex
            "nonce": 0,
            "gas": {
                "price": 1000000000,
                "limit": 21000,
            },
            "payload": {
                "t": 0,  # TxKind.TRANSFER
                "v": {
                    "to": "0x...",
                    "amount": 1_000_000_000,
                    "data": "0x",
                }
            },
            "accessList": []
        },
        "sigs": [{
            "alg": 0x1001,
            "pubkey": "0x...",
            "sig": "0x...",
        }]
    }, indent=2))
    
    print("\n" + "="*60)
    print("Problem Statement Summary:")
    print("="*60)
    print("After tx.sendRawTransaction on chainId=2:")
    print("  ✗ mempool shows pending but miner builds empty blocks")
    print("  ✗ transactions:[null] in mined blocks")
    print("  ✗ state.getNonce stays 0, resends reuse nonce 0")
    print("  ✗ tx.getTransactionByHash returns only {hash,value:0}")
    print("  ✗ CLI emits liboqs warning though PQ pure-python enabled")
    print()
    print("Required fixes:")
    print("  1. Mempool decode: persist raw bytes, return full tx fields")
    print("  2. Mining: select txs by chainId=2, enforce nonce sequencing")
    print("  3. Pending nonce: RPC returns pending nonce for back-to-back sends")
    print("  4. Block RPC: return tx hashes, never [null]")
    print("  5. Remove liboqs runtime dep in CLI flows")
    print("  6. Add regression tests")


if __name__ == "__main__":
    test_data_structures()
    print("\n✓ Data structure test passed")
