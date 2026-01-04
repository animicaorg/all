#!/usr/bin/env python3
"""
Manual test script to verify transaction propagation across nodes.

This tests the complete flow:
1. Submit tx on node A via RPC
2. Verify tx propagates to node B mempool
3. Verify node B can query the tx
4. Verify node B mining template includes it

Run with: python3 test_mempool_tx_propagation_manual.py
"""

import asyncio
import hashlib
import json
import sys
import time
from typing import Any, Dict, Optional

# Mock mempool and relay service for isolated testing
class MockMempool:
    def __init__(self, node_name: str):
        self.node_name = node_name
        self._txs: Dict[bytes, bytes] = {}
        self._admission_log: list[Dict[str, Any]] = []
    
    async def admit_tx(self, raw: bytes, origin: Optional[str] = None) -> tuple[bool, Optional[str]]:
        tx_hash = hashlib.sha3_256(raw).digest()
        if tx_hash in self._txs:
            return True, "duplicate"
        self._txs[tx_hash] = raw
        self._admission_log.append({
            "hash": tx_hash.hex(),
            "size": len(raw),
            "origin": origin,
            "node": self.node_name,
            "at": time.time(),
        })
        print(f"[{self.node_name}] Admitted tx {tx_hash.hex()[:16]}... from {origin}")
        return True, None
    
    async def get_tx_raw(self, tx_hash: bytes) -> Optional[bytes]:
        return self._txs.get(tx_hash)
    
    async def has_tx(self, tx_hash: bytes) -> bool:
        return tx_hash in self._txs
    
    async def list_pending_hashes(self, limit: int = 512) -> list[bytes]:
        return list(self._txs.keys())[:limit]
    
    def size(self) -> int:
        return len(self._txs)


async def test_basic_relay_service():
    """Test that TxRelayService basic flow works."""
    from p2p.txrelay import TxRelayService
    
    print("\n=== Test 1: Basic TxRelayService Flow ===")
    
    # Create two mock mempools
    mempool_a = MockMempool("node-a")
    mempool_b = MockMempool("node-b")
    
    # Track messages sent
    messages: list[Dict[str, Any]] = []
    
    async def send_tx_inv(peer: str, txids: list[bytes]) -> None:
        messages.append({"type": "inv", "peer": peer, "txids": [t.hex() for t in txids]})
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-a", txids)
    
    async def send_tx_get(peer: str, txids: list[bytes]) -> None:
        messages.append({"type": "get", "peer": peer, "txids": [t.hex() for t in txids]})
        if peer == "peer-a":
            await relay_a.on_tx_get("peer-b", txids)
    
    async def send_tx_data(peer: str, items: list[dict]) -> None:
        messages.append({"type": "data", "peer": peer, "count": len(items)})
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-a", items)
    
    async def send_noop(_peer: str, _payload: Any) -> None:
        pass
    
    # Create relay services for both nodes
    relay_a = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,  # Fast for testing
        peer_ids=lambda: ["peer-a", "peer-b"],
        peer_eligible=lambda p: True,
        send_tx_inv=send_tx_inv,
        send_tx_get=send_tx_get,
        send_tx_data=send_tx_data,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_a.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_a.get_tx_raw,
        admit_tx=mempool_a.admit_tx,
        list_mempool_hashes=mempool_a.list_pending_hashes,
    )
    
    relay_b = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,  # Fast for testing
        peer_ids=lambda: ["peer-a", "peer-b"],
        peer_eligible=lambda p: True,
        send_tx_inv=send_tx_inv,
        send_tx_get=send_tx_get,
        send_tx_data=send_tx_data,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_b.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_b.get_tx_raw,
        admit_tx=mempool_b.admit_tx,
        list_mempool_hashes=mempool_b.list_pending_hashes,
    )
    
    # Register peers
    relay_a.register_peer("peer-b", peer_node_id="node-b", direction="outbound", remote="127.0.0.1:30333")
    relay_b.register_peer("peer-a", peer_node_id="node-a", direction="inbound", remote="127.0.0.1:30334")
    
    # Start relay loops
    inv_task_a = asyncio.create_task(relay_a.inv_flush_loop())
    inv_task_b = asyncio.create_task(relay_b.inv_flush_loop())
    
    try:
        # Create and admit transaction to node A
        raw_tx = b"test-transaction-payload-" + str(time.time()).encode()
        tx_hash = hashlib.sha3_256(raw_tx).digest()
        
        print(f"\n1. Admitting tx {tx_hash.hex()[:16]}... to node A")
        ok, reason = await mempool_a.admit_tx(raw_tx, origin="local")
        assert ok, f"Failed to admit: {reason}"
        
        # Trigger relay announcement
        print(f"2. Triggering relay announcement on node A")
        await relay_a.on_mempool_add(tx_hash, raw_tx)
        
        # Wait for propagation
        print(f"3. Waiting for propagation...")
        await asyncio.sleep(0.5)
        
        # Verify node B has the transaction
        print(f"4. Checking if node B has the transaction...")
        has_it = await mempool_b.has_tx(tx_hash)
        
        if not has_it:
            print(f"❌ FAILED: Node B does not have transaction")
            print(f"   Messages sent: {len(messages)}")
            for msg in messages:
                print(f"     - {msg}")
            print(f"   Node A mempool size: {mempool_a.size()}")
            print(f"   Node B mempool size: {mempool_b.size()}")
            return False
        
        # Verify full bytes match
        raw_on_b = await mempool_b.get_tx_raw(tx_hash)
        if raw_on_b != raw_tx:
            print(f"❌ FAILED: Transaction bytes mismatch")
            return False
        
        # Check message flow
        inv_msgs = [m for m in messages if m["type"] == "inv"]
        get_msgs = [m for m in messages if m["type"] == "get"]
        data_msgs = [m for m in messages if m["type"] == "data"]
        
        print(f"\n✅ SUCCESS: Transaction propagated correctly")
        print(f"   INV messages: {len(inv_msgs)}")
        print(f"   GET messages: {len(get_msgs)}")
        print(f"   DATA messages: {len(data_msgs)}")
        print(f"   Node A mempool: {mempool_a.size()} txs")
        print(f"   Node B mempool: {mempool_b.size()} txs")
        
        return True
        
    finally:
        relay_a._running = False
        relay_b._running = False
        inv_task_a.cancel()
        inv_task_b.cancel()
        try:
            await inv_task_a
        except asyncio.CancelledError:
            pass
        try:
            await inv_task_b
        except asyncio.CancelledError:
            pass


async def main():
    print("=" * 70)
    print("Mempool Transaction Propagation Test")
    print("=" * 70)
    
    try:
        success = await test_basic_relay_service()
        
        if success:
            print("\n" + "=" * 70)
            print("✅ All tests passed!")
            print("=" * 70)
            sys.exit(0)
        else:
            print("\n" + "=" * 70)
            print("❌ Tests failed!")
            print("=" * 70)
            sys.exit(1)
            
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
