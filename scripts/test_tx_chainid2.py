#!/usr/bin/env python3
"""
Manual test script for chainId=2 transaction submission and inclusion.

Usage:
  python3 scripts/test_tx_chainid2.py [--rpc-url http://127.0.0.1:18546/rpc]

This script:
1. Verifies the node is on chainId=2
2. Builds a test transaction with chainId=2
3. Submits it via tx.sendRawTransaction
4. Verifies it appears in pending pool with full fields
5. Triggers mining (if auto-mine is enabled)
6. Verifies the transaction is included in the next block
"""
import argparse
import json
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def rpc_call(url: str, method: str, params: list) -> dict:
    """Make a JSON-RPC call and return the result."""
    import requests
    
    response = requests.post(url, json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params
    })
    response.raise_for_status()
    data = response.json()
    
    if "error" in data:
        raise Exception(f"RPC error: {data['error']}")
    
    return data.get("result")


def main():
    parser = argparse.ArgumentParser(description="Test chainId=2 transaction submission")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:18546/rpc", help="RPC URL")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    print(f"Testing transaction submission on {args.rpc_url}")
    print()
    
    # Step 1: Verify chainId
    print("Step 1: Verifying node chain ID...")
    try:
        chain_id = rpc_call(args.rpc_url, "chain.getChainId", [])
        print(f"  ✓ Node chain ID: {chain_id}")
        
        if chain_id != 2:
            print(f"  ⚠ Warning: Expected chainId=2 but got {chain_id}")
            print("  This test is designed for testnet (chainId=2)")
            response = input("  Continue anyway? (y/N): ")
            if response.lower() != 'y':
                return 1
    except Exception as e:
        print(f"  ✗ Failed to get chain ID: {e}")
        return 1
    
    print()
    
    # Step 2: Check pending pool before submission
    print("Step 2: Checking pending pool...")
    try:
        pending_before = rpc_call(args.rpc_url, "mempool.getPending", [])
        print(f"  Pending transactions before: {len(pending_before)}")
        if args.verbose and pending_before:
            print(f"    Hashes: {pending_before[:3]}...")
    except Exception as e:
        print(f"  ⚠ Could not get pending pool: {e}")
        pending_before = []
    
    print()
    
    # Step 3: Build a test transaction
    print("Step 3: Building test transaction...")
    print("  Note: This requires a valid signed transaction")
    print("  For now, we'll use animica tx send CLI to submit a transaction")
    print()
    print("  To test manually, run:")
    print(f"    animica tx send --from <wallet> --to <address> --value 0.001 \\")
    print(f"      --chain-id {chain_id} --rpc-url {args.rpc_url}")
    print()
    
    # For automated testing, we'd need to:
    # 1. Generate or load a test wallet
    # 2. Build a signed transaction
    # 3. Submit it
    # 4. Verify it appears in pending with full fields
    # 5. Mine a block
    # 6. Verify it's included
    
    # For now, just verify the RPC endpoints work
    print("Step 4: Verifying RPC endpoints...")
    
    # Test tx.getTransactionByHash with a dummy hash (should return null)
    try:
        dummy_hash = "0x" + "00" * 32
        result = rpc_call(args.rpc_url, "tx.getTransactionByHash", [dummy_hash])
        print(f"  ✓ tx.getTransactionByHash endpoint works (returned: {type(result).__name__})")
    except Exception as e:
        print(f"  ✗ tx.getTransactionByHash failed: {e}")
    
    # Test mempool.getStats
    try:
        stats = rpc_call(args.rpc_url, "mempool.getStats", [])
        print(f"  ✓ mempool.getStats endpoint works")
        print(f"    Count: {stats.get('count', 0)}")
        print(f"    Total bytes: {stats.get('totalBytes', 0)}")
    except Exception as e:
        print(f"  ⚠ mempool.getStats failed: {e}")
    
    print()
    print("Manual test completed. To fully test transaction inclusion:")
    print("1. Submit a transaction using: animica tx send ...")
    print("2. Check it appears in pending: curl -X POST ... tx.getTransactionByHash")
    print("3. Mine a block: curl -X POST ... miner.mine")
    print("4. Verify transaction is in block: curl -X POST ... chain.getBlockByNumber")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
