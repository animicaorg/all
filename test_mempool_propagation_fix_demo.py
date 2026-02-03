#!/usr/bin/env python3
"""
Demonstration of the mempool propagation fix.

This script shows the before/after behavior:

BEFORE FIX (with 'and local' condition):
- Node A submits tx locally → broadcasts to B ✅
- Node B receives from A → does NOT broadcast to C ❌
- Node C never receives the transaction

AFTER FIX (without 'and local' condition):
- Node A submits tx locally → broadcasts to B ✅
- Node B receives from A → broadcasts to C ✅
- Node C receives the transaction ✅

The fix enables multi-hop propagation across the P2P network.
"""

import asyncio
import hashlib
import time
from typing import Any, Optional


class MockMempool:
    def __init__(self, node_name: str):
        self.node_name = node_name
        self._txs: dict[bytes, bytes] = {}
    
    async def admit_tx(self, raw: bytes, origin: Optional[str] = None) -> tuple[bool, Optional[str]]:
        tx_hash = hashlib.sha3_256(raw).digest()
        if tx_hash in self._txs:
            return True, "duplicate"
        self._txs[tx_hash] = raw
        print(f"  [{self.node_name}] ✓ Admitted tx {tx_hash.hex()[:16]}... from {origin or 'local'}")
        return True, None
    
    async def has_tx(self, tx_hash: bytes) -> bool:
        return tx_hash in self._txs
    
    async def get_tx_raw(self, tx_hash: bytes) -> Optional[bytes]:
        return self._txs.get(tx_hash)
    
    async def list_pending_hashes(self, limit: int = 512) -> list[bytes]:
        return list(self._txs.keys())[:limit]


async def test_propagation():
    """Test that shows transactions propagate across 3 nodes."""
    from p2p.txrelay import TxRelayService
    
    print("\n" + "=" * 70)
    print("Mempool Propagation Fix Demonstration")
    print("=" * 70)
    print("\nNetwork Topology: Node A <--> Node B <--> Node C")
    print("                    (seed)      (relay)     (leaf)")
    print()
    
    # Create three mempools
    mempool_a = MockMempool("Node-A")
    mempool_b = MockMempool("Node-B")
    mempool_c = MockMempool("Node-C")
    
    # Track message flow
    message_log = []
    
    # Message routing
    async def send_tx_inv_a(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "A", "to": peer, "type": "INV"})
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-a", txids)
    
    async def send_tx_inv_b(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "B", "to": peer, "type": "INV"})
        if peer == "peer-a":
            await relay_a.on_tx_inv("peer-b", txids)
        elif peer == "peer-c":
            await relay_c.on_tx_inv("peer-b", txids)
    
    async def send_tx_inv_c(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "C", "to": peer, "type": "INV"})
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-c", txids)
    
    async def send_tx_get_a(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "A", "to": peer, "type": "GET"})
        if peer == "peer-b":
            await relay_b.on_tx_get("peer-a", txids)
    
    async def send_tx_get_b(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "B", "to": peer, "type": "GET"})
        if peer == "peer-a":
            await relay_a.on_tx_get("peer-b", txids)
        elif peer == "peer-c":
            await relay_c.on_tx_get("peer-b", txids)
    
    async def send_tx_get_c(peer: str, txids: list[bytes]) -> None:
        message_log.append({"from": "C", "to": peer, "type": "GET"})
        if peer == "peer-b":
            await relay_b.on_tx_get("peer-c", txids)
    
    async def send_tx_data_a(peer: str, items: list[dict]) -> None:
        message_log.append({"from": "A", "to": peer, "type": "DATA"})
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-a", items)
    
    async def send_tx_data_b(peer: str, items: list[dict]) -> None:
        message_log.append({"from": "B", "to": peer, "type": "DATA"})
        if peer == "peer-a":
            await relay_a.on_tx_data("peer-b", items)
        elif peer == "peer-c":
            await relay_c.on_tx_data("peer-b", items)
    
    async def send_tx_data_c(peer: str, items: list[dict]) -> None:
        message_log.append({"from": "C", "to": peer, "type": "DATA"})
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-c", items)
    
    async def send_noop(_peer: str, _payload: Any) -> None:
        pass
    
    # Create relay services
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
        # Create transaction and submit to Node A
        raw_tx = b"demo-transaction-" + str(time.time()).encode()
        tx_hash = hashlib.sha3_256(raw_tx).digest()
        
        print("Step 1: Submit transaction to Node A (seed node)")
        print(f"        TX hash: {tx_hash.hex()[:16]}...")
        print()
        
        ok, reason = await mempool_a.admit_tx(raw_tx, origin="local")
        assert ok, f"Failed to admit: {reason}"
        
        # Trigger relay announcement from Node A
        print("Step 2: Node A broadcasts INV to Node B")
        await relay_a.on_mempool_add(tx_hash, raw_tx)
        
        # Wait for propagation
        print("Step 3: Waiting for multi-hop propagation...")
        print()
        await asyncio.sleep(1.0)
        
        # Check results
        has_a = await mempool_a.has_tx(tx_hash)
        has_b = await mempool_b.has_tx(tx_hash)
        has_c = await mempool_c.has_tx(tx_hash)
        
        print("Step 4: Verify transaction reached all nodes")
        print()
        print(f"  Node A: {'✅ Has transaction' if has_a else '❌ Missing'}")
        print(f"  Node B: {'✅ Has transaction' if has_b else '❌ Missing'}")
        print(f"  Node C: {'✅ Has transaction' if has_c else '❌ Missing'}")
        print()
        
        print("Message Flow:")
        for msg in message_log:
            print(f"  {msg['from']} → {msg['to']}: {msg['type']}")
        print()
        
        if has_a and has_b and has_c:
            print("=" * 70)
            print("✅ SUCCESS: Multi-hop propagation working!")
            print("=" * 70)
            print("\nThe fix enables transactions to propagate across the entire network:")
            print("  • Node A (seed) → Node B (relay) → Node C (leaf)")
            print("  • Without the fix, Node C would not receive the transaction")
            print("  • With the fix, all nodes have the transaction in their mempool")
            return True
        else:
            print("=" * 70)
            print("❌ FAILED: Multi-hop propagation not working")
            print("=" * 70)
            return False
        
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
        success = await test_propagation()
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
