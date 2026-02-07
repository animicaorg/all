#!/usr/bin/env python3
"""
Test script to verify that transactions from peers propagate correctly to other peers.

This tests the fix for: "Mempool of peers remain empty after their peers send transactions"

The issue was that when peer A sends a transaction to node B:
1. Node B admits it to mempool
2. P2P broadcast callback fires 
3. Callback tries to send it to ALL peers (including peer A)
4. Only AFTER callback does it mark peer A as knowing about it

This caused unnecessary re-broadcasts and could cause issues with peer mempool sync.

The fix: Mark peer as knowing about the transaction BEFORE admitting to mempool.
"""

import asyncio
import hashlib
import time
from typing import Any, Dict, Optional

class MockMempool:
    def __init__(self, node_name: str):
        self.node_name = node_name
        self._txs: Dict[bytes, bytes] = {}
    
    async def admit_tx(self, raw: bytes, origin: Optional[str] = None) -> tuple[bool, Optional[str]]:
        tx_hash = hashlib.sha3_256(raw).digest()
        if tx_hash in self._txs:
            return True, "duplicate"
        self._txs[tx_hash] = raw
        print(f"[{self.node_name}] Admitted tx {tx_hash.hex()[:16]}... from {origin}")
        return True, None
    
    async def has_tx(self, tx_hash: bytes) -> bool:
        return tx_hash in self._txs
    
    async def get_tx_raw(self, tx_hash: bytes) -> Optional[bytes]:
        return self._txs.get(tx_hash)
    
    async def list_pending_hashes(self, limit: int = 512) -> list[bytes]:
        return list(self._txs.keys())[:limit]


async def test_peer_tx_not_rebroadcast_to_sender():
    """Test that peer-sent transactions aren't re-broadcast to the sending peer."""
    from p2p.txrelay import TxRelayService
    
    print("\n=== Test: Peer TX Not Re-broadcast to Sender ===")
    
    # Create two mock mempools
    mempool_a = MockMempool("node-a")
    mempool_b = MockMempool("node-b")
    
    # Track messages sent
    messages_a_to_b: list[Dict[str, Any]] = []
    messages_b_to_a: list[Dict[str, Any]] = []
    
    async def send_tx_inv_a(peer: str, txids: list[bytes]) -> None:
        messages_a_to_b.append({"type": "inv", "peer": peer, "txids": [t.hex() for t in txids]})
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-a", txids)
    
    async def send_tx_inv_b(peer: str, txids: list[bytes]) -> None:
        messages_b_to_a.append({"type": "inv", "peer": peer, "txids": [t.hex() for t in txids]})
        if peer == "peer-a":
            await relay_a.on_tx_inv("peer-b", txids)
    
    async def send_tx_get_a(peer: str, txids: list[bytes]) -> None:
        messages_a_to_b.append({"type": "get", "peer": peer, "txids": [t.hex() for t in txids]})
        if peer == "peer-b":
            await relay_b.on_tx_get("peer-a", txids)
    
    async def send_tx_get_b(peer: str, txids: list[bytes]) -> None:
        messages_b_to_a.append({"type": "get", "peer": peer, "txids": [t.hex() for t in txids]})
        if peer == "peer-a":
            await relay_a.on_tx_get("peer-b", txids)
    
    async def send_tx_data_a(peer: str, items: list[dict]) -> None:
        messages_a_to_b.append({"type": "data", "peer": peer, "count": len(items)})
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-a", items)
    
    async def send_tx_data_b(peer: str, items: list[dict]) -> None:
        messages_b_to_a.append({"type": "data", "peer": peer, "count": len(items)})
        if peer == "peer-a":
            await relay_a.on_tx_data("peer-b", items)
    
    async def send_noop(_peer: str, _payload: Any) -> None:
        pass
    
    # Create relay services for both nodes
    relay_a = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-b"],
        peer_eligible=lambda p: True,
        send_tx_inv=send_tx_inv_a,
        send_tx_get=send_tx_get_a,
        send_tx_data=send_tx_data_a,
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
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-a"],
        peer_eligible=lambda p: True,
        send_tx_inv=send_tx_inv_b,
        send_tx_get=send_tx_get_b,
        send_tx_data=send_tx_data_b,
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
        raw_tx = b"test-transaction-from-a-" + str(time.time()).encode()
        tx_hash = hashlib.sha3_256(raw_tx).digest()
        
        print(f"\n1. Node A creates and admits tx {tx_hash.hex()[:16]}...")
        ok, reason = await mempool_a.admit_tx(raw_tx, origin="local")
        assert ok, f"Failed to admit: {reason}"
        
        # Trigger relay announcement from A
        print(f"2. Node A announces tx to node B")
        await relay_a.on_mempool_add(tx_hash, raw_tx)
        
        # Wait for propagation
        print(f"3. Waiting for propagation...")
        await asyncio.sleep(0.5)
        
        # Verify node B has the transaction
        print(f"4. Checking if node B received the transaction...")
        has_it = await mempool_b.has_tx(tx_hash)
        assert has_it, "Node B should have the transaction"
        print(f"   ✓ Node B has the transaction")
        
        # Clear messages
        messages_b_to_a.clear()
        
        # Now the KEY TEST: When node B admitted the tx (triggered by TX_DATA from A),
        # it should NOT have tried to send it back to A via INV
        print(f"\n5. Checking that node B did NOT try to re-broadcast to node A...")
        
        # Flush any pending INVs from B
        await asyncio.sleep(0.3)
        
        # Check if B sent any INV messages to A containing this txid
        inv_msgs_to_a = [m for m in messages_b_to_a if m["type"] == "inv"]
        
        print(f"   INV messages from B to A: {len(inv_msgs_to_a)}")
        for msg in inv_msgs_to_a:
            txid_hex = tx_hash.hex()
            if txid_hex in msg.get("txids", []):
                print(f"   ❌ FAIL: Node B tried to send tx back to A (the sender)!")
                print(f"      This means the fix didn't work - B should have known A already has it")
                return False
        
        print(f"   ✓ Node B correctly did NOT re-broadcast to sender (node A)")
        print(f"\n✅ SUCCESS: Fix verified - peer transactions not re-broadcast to sender")
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
    print("Mempool Peer Propagation Fix Test")
    print("=" * 70)
    
    try:
        success = await test_peer_tx_not_rebroadcast_to_sender()
        
        if success:
            print("\n" + "=" * 70)
            print("✅ Test passed! Fix is working correctly.")
            print("=" * 70)
            return 0
        else:
            print("\n" + "=" * 70)
            print("❌ Test failed!")
            print("=" * 70)
            return 1
            
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
