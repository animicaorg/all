#!/usr/bin/env python3
"""
Manual verification script for transaction propagation across nodes.

This script helps debug tx propagation issues by:
1. Connecting to two nodes via RPC
2. Submitting a tx on node A
3. Checking if it appears in node B's mempool
4. Verifying P2P connectivity and relay state

Usage:
    python verify_tx_propagation.py --node-a http://localhost:8545 --node-b http://localhost:8546
"""

import argparse
import asyncio
import json
import sys
import time
from typing import Any, Dict, Optional

try:
    import aiohttp
except ImportError:
    print("Error: aiohttp not installed. Run: pip install aiohttp")
    sys.exit(1)


def extract_tx_hash(tx: Dict[str, Any]) -> Optional[str]:
    """Extract transaction hash from various response formats."""
    return tx.get("hash") or tx.get("txHash")


class RPCClient:
    def __init__(self, url: str):
        self.url = url
        self._req_id = 1

    async def call(self, method: str, params: Any = None) -> Any:
        """Make an RPC call."""
        if params is None:
            params = []
        elif not isinstance(params, list):
            params = [params]
        
        payload = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": method,
            "params": params
        }
        self._req_id += 1
        
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.url}/rpc", json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"RPC error: {resp.status} - {text}")
                result = await resp.json()
                if "error" in result:
                    raise Exception(f"RPC error: {result['error']}")
                return result.get("result")


async def check_p2p_peers(client: RPCClient, node_name: str) -> dict:
    """Check P2P peer connectivity."""
    try:
        peers = await client.call("p2p.listPeers", [])
        print(f"✓ {node_name} P2P peers: {len(peers)} connected")
        return {"count": len(peers), "peers": peers}
    except Exception as e:
        print(f"✗ {node_name} P2P check failed: {e}")
        return {"count": 0, "peers": [], "error": str(e)}


async def check_mempool(client: RPCClient, node_name: str) -> dict:
    """Check mempool state."""
    try:
        result = await client.call("mempool.list", [])
        tx_count = len(result.get("transactions", []))
        print(f"  {node_name} mempool: {tx_count} transactions")
        return result
    except Exception as e:
        print(f"  {node_name} mempool check failed: {e}")
        return {"transactions": [], "error": str(e)}


async def get_tx_relay_snapshot(client: RPCClient, node_name: str) -> Optional[dict]:
    """Get tx relay service snapshot."""
    try:
        result = await client.call("p2p.status", [])
        tx_relay = result.get("tx_relay_v2", {})
        if tx_relay:
            print(f"  {node_name} tx_relay enabled: {tx_relay.get('enabled')}")
            snapshot = tx_relay.get("snapshot", {})
            print(f"    - inflight: {snapshot.get('inflight', 0)}")
            print(f"    - peers: {len(snapshot.get('peers', []))}")
        return tx_relay
    except Exception as e:
        print(f"  {node_name} tx_relay check failed: {e}")
        return None


async def check_tx_in_mempool(client: RPCClient, tx_hash: str, node_name: str) -> bool:
    """Check if a specific tx is in the mempool."""
    try:
        result = await client.call("mempool.list", [])
        txs = result.get("transactions", [])
        for tx in txs:
            if extract_tx_hash(tx) == tx_hash:
                print(f"✓ {node_name} has tx in mempool: {tx_hash}")
                return True
        print(f"✗ {node_name} does NOT have tx in mempool: {tx_hash}")
        return False
    except Exception as e:
        print(f"✗ {node_name} mempool check failed: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="Verify tx propagation between nodes")
    parser.add_argument("--node-a", default="http://localhost:8545", help="Node A RPC URL")
    parser.add_argument("--node-b", default="http://localhost:8546", help="Node B RPC URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="Propagation timeout (seconds)")
    parser.add_argument("--tx-hash", help="Check propagation of existing tx hash instead of submitting new tx")
    args = parser.parse_args()
    
    print("=" * 60)
    print("Transaction Propagation Verification")
    print("=" * 60)
    print(f"Node A: {args.node_a}")
    print(f"Node B: {args.node_b}")
    print()
    
    client_a = RPCClient(args.node_a)
    client_b = RPCClient(args.node_b)
    
    # Step 1: Check P2P connectivity
    print("Step 1: Checking P2P connectivity...")
    peers_a = await check_p2p_peers(client_a, "Node A")
    peers_b = await check_p2p_peers(client_b, "Node B")
    print()
    
    if peers_a["count"] == 0:
        print("⚠ Node A has no peers - propagation will fail")
        print("  Check: docker-compose.multinode.yml seeds configuration")
    if peers_b["count"] == 0:
        print("⚠ Node B has no peers - propagation will fail")
    
    # Step 2: Check initial mempool state
    print("Step 2: Checking initial mempool state...")
    await check_mempool(client_a, "Node A")
    await check_mempool(client_b, "Node B")
    print()
    
    # Step 3: Check tx relay service
    print("Step 3: Checking tx relay service...")
    relay_a = await get_tx_relay_snapshot(client_a, "Node A")
    relay_b = await get_tx_relay_snapshot(client_b, "Node B")
    print()
    
    if relay_a and not relay_a.get("enabled"):
        print("⚠ Node A tx relay is DISABLED")
        print("  Set: ANIMICA_P2P_TX_RELAY=1")
    if relay_b and not relay_b.get("enabled"):
        print("⚠ Node B tx relay is DISABLED")
    
    # Step 4: Get or wait for tx hash
    if args.tx_hash:
        print(f"Step 4: Using provided tx hash: {args.tx_hash}")
        tx_hash = args.tx_hash
        # Check if it's in node A
        if not await check_tx_in_mempool(client_a, tx_hash, "Node A"):
            print("  ⚠ Tx not in Node A mempool - it may have been mined")
        print()
    else:
        print("Step 4: Waiting for new transaction on Node A...")
        print("  Please submit a transaction to Node A")
        print("  Example: animica tx send --to 0x... --value 1000")
        print()
        print("  Monitoring Node A mempool for new transactions...")
        
        # Get baseline mempool
        initial = await check_mempool(client_a, "Node A")
        initial_hashes = {extract_tx_hash(tx) for tx in initial.get("transactions", [])}
        
        # Wait for a new tx
        timeout = 60.0  # 1 minute to submit
        start = time.time()
        tx_hash = None
        while time.time() - start < timeout:
            current = await check_mempool(client_a, "Node A")
            current_hashes = {extract_tx_hash(tx) for tx in current.get("transactions", [])}
            new_hashes = current_hashes - initial_hashes
            if new_hashes:
                tx_hash = list(new_hashes)[0]
                print(f"  ✓ Detected new tx: {tx_hash}")
                break
            await asyncio.sleep(1.0)
        
        if not tx_hash:
            print(f"  ✗ No new transaction detected in {timeout}s")
            print("  Hint: Use --tx-hash <hash> to check an existing transaction")
            return 1
        print()
    
    # Step 5: Wait and check for propagation to Node B
    print(f"Step 5: Waiting up to {args.timeout}s for propagation to Node B...")
    start = time.time()
    found = False
    while time.time() - start < args.timeout:
        if await check_tx_in_mempool(client_b, tx_hash, "Node B"):
            found = True
            break
        await asyncio.sleep(0.5)
    
    elapsed = time.time() - start
    print()
    
    # Final verdict
    print("=" * 60)
    if found:
        print(f"✓ SUCCESS: Transaction propagated in {elapsed:.1f}s")
        print("  - Tx submitted on Node A")
        print("  - Tx appeared in Node B mempool")
        print("  - P2P relay is working correctly")
        return 0
    else:
        print(f"✗ FAIL: Transaction did NOT propagate after {elapsed:.1f}s")
        print()
        print("Possible issues:")
        if peers_a["count"] == 0 or peers_b["count"] == 0:
            print("  1. Nodes not connected to each other (no P2P peers)")
            print("     Fix: Check seeds in docker-compose or network config")
        if relay_a and not relay_a.get("enabled"):
            print("  2. Tx relay disabled on one or both nodes")
            print("     Fix: Set ANIMICA_P2P_TX_RELAY=1")
        print("  3. Check node logs for tx admission errors")
        print("  4. Check node logs for P2P relay errors (TX_INV, TX_GET, TX_DATA)")
        print()
        print("Debug commands:")
        print(f"  docker-compose logs node1 | grep -E 'TX_|TXIDS_'")
        print(f"  docker-compose logs node2 | grep -E 'TX_|TXIDS_'")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
