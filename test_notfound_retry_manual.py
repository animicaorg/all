#!/usr/bin/env python3
"""
Manual integration test to demonstrate the TX_NOTFOUND retry fix.

This test simulates the scenario from the bug report:
- Multiple peers report knowing about a transaction
- First peer responds with NOTFOUND
- System should retry with other peers
"""

import asyncio
import hashlib

from p2p.txrelay import TxRelayService


async def main():
    print("=" * 70)
    print("TX_NOTFOUND Retry Fix Integration Test")
    print("=" * 70)
    
    # Track what messages are sent
    sent_messages = []
    
    async def send_inv(peer: str, txids: list):
        sent_messages.append(("INV", peer, txids))
    
    async def send_get(peer: str, txids: list):
        sent_messages.append(("GET", peer, txids))
        print(f"→ Sent TX_GET to {peer} for {len(txids)} tx(s)")
    
    async def send_data(peer: str, items: list):
        sent_messages.append(("DATA", peer, items))
        print(f"→ Sent TX_DATA to {peer} with {len(items)} tx(s)")
    
    async def send_notfound(peer: str, txids: list):
        sent_messages.append(("NOTFOUND", peer, txids))
        print(f"→ Sent TX_NOTFOUND to {peer} for {len(txids)} tx(s)")
    
    async def send_noop(_peer: str, _payload):
        return None
    
    # Simulate mempool state
    mempool_txs = set()
    
    async def has_tx(txid: bytes) -> bool:
        return txid in mempool_txs
    
    async def has_chain_tx(_txid: bytes) -> bool:
        return False
    
    # peer-b has the transaction, peer-a doesn't
    test_tx_raw = b"test-transaction-data-123"
    test_txid = hashlib.sha3_256(test_tx_raw).digest()
    peer_b_txs = {test_txid: test_tx_raw}
    
    async def get_tx_raw(txid: bytes):
        # Only peer-b has the transaction
        return peer_b_txs.get(txid)
    
    admitted_txs = []
    
    async def admit_tx(raw: bytes, origin: str | None):
        admitted_txs.append((raw, origin))
        mempool_txs.add(hashlib.sha3_256(raw).digest())
        print(f"✓ Transaction admitted to mempool from {origin}")
        return True, None
    
    async def list_hashes(_limit: int):
        return list(mempool_txs)
    
    relay = TxRelayService(
        max_tx_bytes=1024,
        peer_ids=lambda: ["peer-a", "peer-b"],
        peer_eligible=lambda _peer: True,
        send_tx_inv=send_inv,
        send_tx_get=send_get,
        send_tx_data=send_data,
        send_tx_notfound=send_notfound,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=has_tx,
        has_chain_tx=has_chain_tx,
        get_tx_raw=get_tx_raw,
        admit_tx=admit_tx,
        list_mempool_hashes=list_hashes,
    )
    
    # Register both peers
    relay.register_peer("peer-a", peer_node_id="node-a")
    relay.register_peer("peer-b", peer_node_id="node-b")
    
    print("\n1. Setup: Both peers report having the transaction")
    print("-" * 70)
    
    # Manually add to known_txids (simulating mempool sync or inv messages)
    relay._peer_state["peer-a"].known_txids.add(test_txid)
    relay._peer_state["peer-b"].known_txids.add(test_txid)
    relay._record_source(test_txid, "peer-a")
    relay._record_source(test_txid, "peer-b")
    
    print(f"   peer-a knows about tx: {test_txid.hex()[:16]}...")
    print(f"   peer-b knows about tx: {test_txid.hex()[:16]}...")
    
    print("\n2. Request transaction from peers")
    print("-" * 70)
    
    requested = await relay.request_missing_known(limit=1, trigger="manual_test")
    print(f"   Requested {requested} transaction(s)")
    
    await asyncio.sleep(0.01)
    
    # Find which peer was asked first
    get_messages = [m for m in sent_messages if m[0] == "GET"]
    assert len(get_messages) == 1, f"Expected 1 GET message, got {len(get_messages)}"
    first_peer = get_messages[0][1]
    other_peer = "peer-b" if first_peer == "peer-a" else "peer-a"
    
    print(f"   First request sent to: {first_peer}")
    
    print("\n3. First peer responds with NOTFOUND")
    print("-" * 70)
    
    await relay.on_tx_notfound(first_peer, [test_txid])
    print(f"   {first_peer} doesn't have the transaction")
    
    # Check that txid was cleared from first peer but not the other
    state_first = relay._peer_state.get(first_peer)
    state_other = relay._peer_state.get(other_peer)
    
    print(f"   {first_peer} still has tx in known_txids: {test_txid in state_first.known_txids}")
    print(f"   {other_peer} still has tx in known_txids: {test_txid in state_other.known_txids}")
    
    await asyncio.sleep(0.01)
    
    print("\n4. System retries with other peer")
    print("-" * 70)
    
    get_messages = [m for m in sent_messages if m[0] == "GET"]
    if len(get_messages) >= 2:
        second_peer = get_messages[1][1]
        print(f"   Retry request sent to: {second_peer}")
        assert second_peer == other_peer, f"Should retry with {other_peer}, got {second_peer}"
        
        # Simulate peer-b responding with TX_DATA
        print(f"\n5. Second peer responds with TX_DATA")
        print("-" * 70)
        
        await relay.on_tx_data(other_peer, [{"txid": test_txid, "tx_bytes": test_tx_raw}])
        await asyncio.sleep(0.01)
        
        if admitted_txs:
            print(f"   ✓ SUCCESS: Transaction was admitted to mempool!")
            print(f"   Transaction data: {admitted_txs[0][0][:20]}...")
            print(f"   Origin: {admitted_txs[0][1]}")
        else:
            print(f"   ✗ FAILURE: Transaction was NOT admitted to mempool")
    else:
        print(f"   ✗ FAILURE: No retry was sent (only {len(get_messages)} GET messages)")
    
    print("\n" + "=" * 70)
    print("Test Complete")
    print("=" * 70)
    
    # Summary
    print("\nSummary:")
    print(f"  Total GET messages sent: {len([m for m in sent_messages if m[0] == 'GET'])}")
    print(f"  Transactions admitted: {len(admitted_txs)}")
    print(f"  Test result: {'PASS ✓' if len(admitted_txs) > 0 else 'FAIL ✗'}")


if __name__ == "__main__":
    asyncio.run(main())
