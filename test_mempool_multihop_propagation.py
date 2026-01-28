#!/usr/bin/env python3
"""
Test that transactions propagate across multiple hops in a network.

This verifies the fix for the mempool broadcasting issue where transactions
were only propagating to immediate neighbors but not further.

Network topology:
    Node A <--> Node B <--> Node C
    
Submit tx to Node A, verify it reaches Node C through Node B.
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
        print(f"[{self.node_name}] ✓ Admitted tx {tx_hash.hex()[:16]}... from {origin or 'local'}")
        return True, None
    
    async def has_tx(self, tx_hash: bytes) -> bool:
        return tx_hash in self._txs
    
    async def get_tx_raw(self, tx_hash: bytes) -> Optional[bytes]:
        return self._txs.get(tx_hash)
    
    async def list_pending_hashes(self, limit: int = 512) -> list[bytes]:
        return list(self._txs.keys())[:limit]
    
    def size(self) -> int:
        return len(self._txs)


async def test_multihop_propagation():
    """Test that transactions propagate across multiple hops."""
    from p2p.txrelay import TxRelayService
    
    print("\n" + "=" * 70)
    print("Multi-Hop Transaction Propagation Test")
    print("=" * 70)
    print("\nNetwork topology: Node A <--> Node B <--> Node C")
    print("Submitting transaction to Node A, expecting propagation to Node C\n")
    
    # Create three mempools
    mempool_a = MockMempool("Node-A")
    mempool_b = MockMempool("Node-B")
    mempool_c = MockMempool("Node-C")
    
    # Track message flow
    message_log = []
    
    # Message routing functions
    async def send_tx_inv_a(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "A", "to": peer, "type": "INV", "txids": len(txids)})
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-a", txids)
    
    async def send_tx_inv_b(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "B", "to": peer, "type": "INV", "txids": len(txids)})
        if peer == "peer-a":
            await relay_a.on_tx_inv("peer-b", txids)
        elif peer == "peer-c":
            await relay_c.on_tx_inv("peer-b", txids)
    
    async def send_tx_inv_c(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "C", "to": peer, "type": "INV", "txids": len(txids)})
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-c", txids)
    
    async def send_tx_get_a(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "A", "to": peer, "type": "GET", "txids": len(txids)})
        if peer == "peer-b":
            await relay_b.on_tx_get("peer-a", txids)
    
    async def send_tx_get_b(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "B", "to": peer, "type": "GET", "txids": len(txids)})
        if peer == "peer-a":
            await relay_a.on_tx_get("peer-b", txids)
        elif peer == "peer-c":
            await relay_c.on_tx_get("peer-b", txids)
    
    async def send_tx_get_c(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "C", "to": peer, "type": "GET", "txids": len(txids)})
        if peer == "peer-b":
            await relay_b.on_tx_get("peer-c", txids)
    
    async def send_tx_data_a(peer: str, items: list[dict]) -> None:
        message_log.append({"from": "A", "to": peer, "type": "DATA", "count": len(items)})
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-a", items)
    
    async def send_tx_data_b(peer: str, items: list[dict]) -> None:
        message_log.append({"from": "B", "to": peer, "type": "DATA", "count": len(items)})
        if peer == "peer-a":
            await relay_a.on_tx_data("peer-b", items)
        elif peer == "peer-c":
            await relay_c.on_tx_data("peer-b", items)
    
    async def send_tx_data_c(peer: str, items: list[dict]) -> None:
        message_log.append({"from": "C", "to": peer, "type": "DATA", "count": len(items)})
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-c", items)
    
    async def send_noop(_peer: str, _payload: Any) -> None:
        pass
    
    # Create relay service for Node A (connected to B)
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
    
    # Create relay service for Node B (connected to A and C)
    relay_b = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-a", "peer-c"],
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
    
    # Create relay service for Node C (connected to B)
    relay_c = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-b"],
        peer_eligible=lambda p: True,
        send_tx_inv=send_tx_inv_c,
        send_tx_get=send_tx_get_c,
        send_tx_data=send_tx_data_c,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_c.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_c.get_tx_raw,
        admit_tx=mempool_c.admit_tx,
        list_mempool_hashes=mempool_c.list_pending_hashes,
    )
    
    # Register peers
    relay_a.register_peer("peer-b", peer_node_id="node-b", direction="outbound", remote="127.0.0.1:30334")
    relay_b.register_peer("peer-a", peer_node_id="node-a", direction="inbound", remote="127.0.0.1:30333")
    relay_b.register_peer("peer-c", peer_node_id="node-c", direction="outbound", remote="127.0.0.1:30335")
    relay_c.register_peer("peer-b", peer_node_id="node-b", direction="inbound", remote="127.0.0.1:30334")
    
    # Start inv flush loops
    inv_task_a = asyncio.create_task(relay_a.inv_flush_loop())
    inv_task_b = asyncio.create_task(relay_b.inv_flush_loop())
    inv_task_c = asyncio.create_task(relay_c.inv_flush_loop())
    
    try:
        # Create transaction and admit to Node A (simulating local submission)
        raw_tx = b"multihop-test-transaction-" + str(time.time()).encode()
        tx_hash = hashlib.sha3_256(raw_tx).digest()
        
        print(f"Step 1: Submitting transaction to Node A")
        print(f"        TX hash: {tx_hash.hex()[:16]}...\n")
        
        ok, reason = await mempool_a.admit_tx(raw_tx, origin="local")
        assert ok, f"Failed to admit to Node A: {reason}"
        
        # Trigger relay announcement from Node A
        print(f"Step 2: Node A broadcasts to its peers (Node B)")
        await relay_a.on_mempool_add(tx_hash, raw_tx)
        
        # Wait for propagation through the network
        print(f"Step 3: Waiting for propagation...\n")
        await asyncio.sleep(1.0)
        
        # Check results
        print("Step 4: Verifying transaction reached all nodes\n")
        
        has_a = await mempool_a.has_tx(tx_hash)
        has_b = await mempool_b.has_tx(tx_hash)
        has_c = await mempool_c.has_tx(tx_hash)
        
        print(f"Node A mempool: {mempool_a.size()} txs - {'✓' if has_a else '✗'}")
        print(f"Node B mempool: {mempool_b.size()} txs - {'✓' if has_b else '✗'}")
        print(f"Node C mempool: {mempool_c.size()} txs - {'✓' if has_c else '✗'}")
        
        print(f"\nMessage flow:")
        for msg in message_log:
            print(f"  {msg['from']} → {msg['to']}: {msg['type']} ({msg.get('txids', msg.get('count', 0))})")
        
        # Verify success
        if not has_b:
            print("\n❌ FAILED: Transaction did not reach Node B")
            return False
        
        if not has_c:
            print("\n❌ FAILED: Transaction did not reach Node C")
            print("   This indicates the mempool broadcasting fix is not working!")
            print("   The fix should ensure Node B broadcasts to Node C after receiving from Node A.")
            return False
        
        # Verify bytes match
        raw_on_c = await mempool_c.get_tx_raw(tx_hash)
        if raw_on_c != raw_tx:
            print("\n❌ FAILED: Transaction bytes mismatch on Node C")
            return False
        
        print("\n" + "=" * 70)
        print("✅ SUCCESS: Transaction propagated across all nodes!")
        print("=" * 70)
        print("\nThe fix works correctly:")
        print("  • Node A submitted tx locally → broadcast to Node B ✓")
        print("  • Node B received from A → broadcast to Node C ✓")
        print("  • Node C received from B → has transaction ✓")
        
        return True
        
    finally:
        relay_a._running = False
        relay_b._running = False
        relay_c._running = False
        for task in [inv_task_a, inv_task_b, inv_task_c]:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def main():
    try:
        success = await test_multihop_propagation()
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
