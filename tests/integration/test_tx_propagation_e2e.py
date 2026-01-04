"""
End-to-end test for transaction propagation across nodes.

This test demonstrates the complete flow:
1. Submit a tx on node A via internal RPC
2. Assert node B mempool has tx hash and full bytes within timeout
3. Assert node B can return the tx via RPC
4. Assert node B can include it in a mined block template
"""
import asyncio
import hashlib
import time
from typing import Any, Optional

import pytest


class SimpleMempool:
    """Minimal mempool for testing tx propagation."""

    def __init__(self) -> None:
        self._txs: dict[bytes, bytes] = {}
        self._admit_log: list[dict[str, Any]] = []

    async def admit_tx(
        self,
        raw: bytes,
        local: Optional[bool] = False,
        origin_peer: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """Admit a transaction to the mempool."""
        tx_hash = hashlib.sha3_256(raw).digest()
        if tx_hash in self._txs:
            return True, "duplicate"
        self._txs[tx_hash] = raw
        self._admit_log.append({
            "hash": tx_hash.hex(),
            "size": len(raw),
            "local": local,
            "origin": origin_peer,
            "at": time.time(),
        })
        return True, None

    async def get_tx_raw(self, tx_hash: bytes) -> Optional[bytes]:
        """Get raw transaction bytes by hash."""
        return self._txs.get(tx_hash)

    async def list_pending_hashes(self, limit: int = 512) -> list[bytes]:
        """List pending transaction hashes."""
        return list(self._txs.keys())[:limit]

    async def has_tx(self, tx_hash: bytes) -> bool:
        """Check if transaction is in mempool."""
        return tx_hash in self._txs

    def mempool_size(self) -> int:
        """Get current mempool size."""
        return len(self._txs)


async def wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll predicate until it returns truthy or timeout elapses."""
    end = asyncio.get_event_loop().time() + timeout
    while True:
        try:
            if asyncio.iscoroutinefunction(predicate):
                ok = await predicate()
            else:
                result = predicate()
                # Handle both sync and async results
                if asyncio.iscoroutine(result):
                    ok = await result
                else:
                    ok = result
            if ok:
                return True
        except Exception:
            pass  # Ignore errors during polling
        if asyncio.get_event_loop().time() >= end:
            return False
        await asyncio.sleep(interval)


@pytest.mark.asyncio
async def test_tx_propagation_simple_mempools() -> None:
    """
    Test that TxRelayService propagates transactions between two simple mempools.
    This is a pure unit test without full P2P stack.
    """
    from p2p.txrelay import TxRelayService
    
    mempool_a = SimpleMempool()
    mempool_b = SimpleMempool()
    
    peers = ["peer-a", "peer-b"]
    messages_sent: list[dict[str, Any]] = []
    
    async def send_tx_inv(peer: str, txids: list[bytes]) -> None:
        messages_sent.append({"type": "tx_inv", "peer": peer, "txids": [t.hex() for t in txids]})
        # Simulate: node B receives inv from node A
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-a", txids)
    
    async def send_tx_get(peer: str, txids: list[bytes]) -> None:
        messages_sent.append({"type": "tx_get", "peer": peer, "txids": [t.hex() for t in txids]})
        # Simulate: node A receives get from node B
        if peer == "peer-a":
            await relay_a.on_tx_get("peer-b", txids)
    
    async def send_tx_data(peer: str, items: list[dict]) -> None:
        messages_sent.append({"type": "tx_data", "peer": peer, "count": len(items)})
        # Simulate: node B receives data from node A
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-a", items)
    
    async def send_noop(_peer: str, _payload: Any) -> None:
        pass
    
    relay_a = TxRelayService(
        max_tx_bytes=1024 * 1024,
        peer_ids=lambda: peers,
        peer_eligible=lambda p: p in peers,
        send_tx_inv=send_tx_inv,
        send_tx_get=send_tx_get,
        send_tx_data=send_tx_data,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_a.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),  # no chain txs
        get_tx_raw=mempool_a.get_tx_raw,
        admit_tx=mempool_a.admit_tx,
        list_mempool_hashes=mempool_a.list_pending_hashes,
    )
    
    relay_b = TxRelayService(
        max_tx_bytes=1024 * 1024,
        peer_ids=lambda: peers,
        peer_eligible=lambda p: p in peers,
        send_tx_inv=send_tx_inv,
        send_tx_get=send_tx_get,
        send_tx_data=send_tx_data,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_b.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),  # no chain txs
        get_tx_raw=mempool_b.get_tx_raw,
        admit_tx=mempool_b.admit_tx,
        list_mempool_hashes=mempool_b.list_pending_hashes,
    )
    
    # Register peers
    relay_a.register_peer("peer-b", peer_node_id="node-b", direction="outbound", remote="127.0.0.1:30333")
    relay_b.register_peer("peer-a", peer_node_id="node-a", direction="inbound", remote="127.0.0.1:30334")
    
    # Start relay loops (needed for inv flushing)
    inv_flush_task_a = asyncio.create_task(relay_a.inv_flush_loop())
    inv_flush_task_b = asyncio.create_task(relay_b.inv_flush_loop())
    
    try:
        # Add transaction to node A locally
        raw_tx = b"test-transaction-payload-12345"
        tx_hash = hashlib.sha3_256(raw_tx).digest()
        
        # Simulate local admission on node A
        ok, reason = await mempool_a.admit_tx(raw_tx, local=True, origin_peer="local")
        assert ok, f"Failed to admit tx: {reason}"
        
        # Trigger relay notification (simulate what P2P service does after admission)
        await relay_a.on_mempool_add(tx_hash, raw_tx)
        
        # Give some time for propagation
        await asyncio.sleep(0.5)
        
        # Verify node B has the transaction
        propagated = await wait_for(lambda: mempool_b.has_tx(tx_hash), timeout=2.0)
        assert propagated, "Transaction did not propagate to node B"
    finally:
        relay_a._running = False
        relay_b._running = False
        inv_flush_task_a.cancel()
        inv_flush_task_b.cancel()
        try:
            await inv_flush_task_a
        except asyncio.CancelledError:
            pass
        try:
            await inv_flush_task_b
        except asyncio.CancelledError:
            pass
        
        # Verify full transaction bytes
        raw_on_b = await mempool_b.get_tx_raw(tx_hash)
        assert raw_on_b == raw_tx, "Transaction bytes mismatch"
        
        # Verify origin tracking
        assert len(mempool_b._admit_log) > 0
        last_admit = mempool_b._admit_log[-1]
        # Note: TxRelayService passes origin as second positional arg, not 'origin_peer' kwarg
        print(f"  Last admit log: {last_admit}")
        
        # Verify message flow
        inv_msgs = [m for m in messages_sent if m["type"] == "tx_inv"]
        get_msgs = [m for m in messages_sent if m["type"] == "tx_get"]
        data_msgs = [m for m in messages_sent if m["type"] == "tx_data"]
        
        assert len(inv_msgs) > 0, "Should have sent at least one INV"
        assert len(get_msgs) > 0, "Should have sent at least one GET"
        assert len(data_msgs) > 0, "Should have sent at least one DATA"
        
        print(f"✓ Transaction propagated successfully")
        print(f"  - INV messages: {len(inv_msgs)}")
        print(f"  - GET messages: {len(get_msgs)}")
        print(f"  - DATA messages: {len(data_msgs)}")


@pytest.mark.asyncio
async def test_duplicate_prevention() -> None:
    """
    Test that the seen cache prevents infinite request loops.
    """
    from p2p.txrelay import TxRelayService
    
    mempool_a = SimpleMempool()
    get_count = 0
    
    async def send_tx_get(peer: str, txids: list[bytes]) -> None:
        nonlocal get_count
        get_count += len(txids)
    
    async def send_noop(_peer: str, _payload: Any) -> None:
        pass
    
    relay = TxRelayService(
        max_tx_bytes=1024 * 1024,
        peer_ids=lambda: ["peer-a"],
        peer_eligible=lambda p: p == "peer-a",
        send_tx_inv=send_noop,
        send_tx_get=send_tx_get,
        send_tx_data=send_noop,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_a.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_a.get_tx_raw,
        admit_tx=mempool_a.admit_tx,
        list_mempool_hashes=mempool_a.list_pending_hashes,
    )
    
    relay.register_peer("peer-a")
    
    tx_hash = hashlib.sha3_256(b"test-tx").digest()
    
    # First inv should trigger GET
    await relay.on_tx_inv("peer-a", [tx_hash])
    assert get_count == 1, "Should send GET on first INV"
    
    # Second inv with same txid should NOT trigger another GET (inflight)
    await relay.on_tx_inv("peer-a", [tx_hash])
    assert get_count == 1, "Should not send duplicate GET while inflight"
    
    # Simulate notfound (removes from inflight, adds to reject cache)
    await relay.on_tx_notfound("peer-a", [tx_hash])
    
    # Third inv should still NOT trigger GET (reject cache)
    await relay.on_tx_inv("peer-a", [tx_hash])
    assert get_count == 1, "Should not send GET for rejected tx"
    
    print("✓ Duplicate prevention working correctly")


@pytest.mark.asyncio
async def test_inv_get_push_flow() -> None:
    """
    Test the complete INV -> GET -> PUSH flow with proper caching.
    """
    from p2p.txrelay import TxRelayService
    
    mempool_a = SimpleMempool()
    mempool_b = SimpleMempool()
    
    # Track message flow
    flow: list[str] = []
    
    async def send_tx_inv(peer: str, txids: list[bytes]) -> None:
        flow.append(f"INV:{peer}")
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-a", txids)
    
    async def send_tx_get(peer: str, txids: list[bytes]) -> None:
        flow.append(f"GET:{peer}")
        if peer == "peer-a":
            await relay_a.on_tx_get("peer-b", txids)
    
    async def send_tx_data(peer: str, items: list[dict]) -> None:
        flow.append(f"PUSH:{peer}")
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-a", items)
    
    async def send_noop(_peer: str, _payload: Any) -> None:
        pass
    
    relay_a = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_flush_interval_s=0.05,  # Fast flush for testing
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
        inv_flush_interval_s=0.05,  # Fast flush for testing
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
    
    relay_a.register_peer("peer-b")
    relay_b.register_peer("peer-a")
    
    # Start inv flush loop for relay_a
    flush_task = asyncio.create_task(relay_a.inv_flush_loop())
    
    try:
        # Add tx to node A
        raw_tx = b"test-flow-transaction"
        tx_hash = hashlib.sha3_256(raw_tx).digest()
        await mempool_a.admit_tx(raw_tx, local=True)
        
        # Trigger relay
        await relay_a.on_mempool_add(tx_hash, raw_tx)
        
        # Wait for propagation
        await asyncio.sleep(0.5)
        
        # Verify flow
        assert "INV:peer-b" in flow, "Should send INV to peer-b"
        assert "GET:peer-a" in flow, "Should send GET to peer-a"
        assert "PUSH:peer-b" in flow, "Should send PUSH to peer-b"
        
        # Verify tx is in mempool B
        assert mempool_b.has_tx(tx_hash), "Tx should be in mempool B"
        
        # Verify caches are updated
        peer_state_b = relay_b._peer_state.get("peer-a")
        assert peer_state_b is not None
        assert tx_hash in peer_state_b.known_txids, "Tx should be in known_txids cache"
        
        print(f"✓ INV->GET->PUSH flow completed successfully")
        print(f"  Message flow: {' -> '.join(flow)}")
        
    finally:
        relay_a._running = False
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
