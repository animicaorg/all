#!/usr/bin/env python3
"""
Test for the specific scenario from the bug report:
- Same peer (node_id) with multiple connections
- Each connection reports knowing about a transaction
- First connection responds NOTFOUND
- Second connection should be tried
"""

import asyncio
import hashlib

from p2p.txrelay import TxRelayService


async def main():
    print("=" * 70)
    print("Bug Report Scenario Test: Same Peer, Multiple Connections")
    print("=" * 70)
    
    sent_messages = []
    
    async def send_get(peer: str, txids: list):
        sent_messages.append(("GET", peer, txids))
        print(f"→ Sent TX_GET to {peer} for tx {txids[0].hex()[:16]}...")
    
    async def send_noop(_peer: str, _payload):
        return None
    
    mempool_txs = set()
    
    async def has_tx(txid: bytes) -> bool:
        return txid in mempool_txs
    
    async def has_chain_tx(_txid: bytes) -> bool:
        return False
    
    # Transaction data
    test_tx_raw = b"transaction-from-peer-0xb11a50ed93"
    test_txid = hashlib.sha3_256(test_tx_raw).digest()
    
    # Connection 0x7e65d4f3-a has the transaction
    conn_a_has_tx = {test_txid: test_tx_raw}
    
    async def get_tx_raw(txid: bytes):
        # Only conn-a has it
        return conn_a_has_tx.get(txid)
    
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
        peer_ids=lambda: ["0x17be5add-d", "0x7e65d4f3-a"],
        peer_eligible=lambda _peer: True,
        send_tx_inv=send_noop,
        send_tx_get=send_get,
        send_tx_data=send_noop,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=has_tx,
        has_chain_tx=has_chain_tx,
        get_tx_raw=get_tx_raw,
        admit_tx=admit_tx,
        list_mempool_hashes=list_hashes,
    )
    
    # Register both connections from the same peer node
    # In the logs: peer=0xb11a50ed93 conn_id=0x17be5add-d
    #              peer=0xb11a50ed93 conn_id=0x7e65d4f3-a
    relay.register_peer("0x17be5add-d", peer_node_id="0xb11a50ed93")
    relay.register_peer("0x7e65d4f3-a", peer_node_id="0xb11a50ed93")
    
    print("\nScenario from logs:")
    print("  peer=0xb11a50ed93 conn_id=0x17be5add-d known_txids=1")
    print("  peer=0xb11a50ed93 conn_id=0x7e65d4f3-a known_txids=1")
    print(f"  sample=[{test_txid.hex()}]")
    print()
    
    # Both connections report having the transaction
    relay._peer_state["0x17be5add-d"].known_txids.add(test_txid)
    relay._peer_state["0x7e65d4f3-a"].known_txids.add(test_txid)
    relay._record_source(test_txid, "0x17be5add-d")
    relay._record_source(test_txid, "0x7e65d4f3-a")
    
    print("1. Request transaction via importPeerKnownTxs")
    print("-" * 70)
    
    requested = await relay.request_missing_known(limit=1, trigger="importPeerKnownTxs", force=True)
    print(f"   Requested {requested} transaction(s)")
    
    await asyncio.sleep(0.01)
    
    get_messages = [m for m in sent_messages if m[0] == "GET"]
    assert len(get_messages) == 1
    first_conn = get_messages[0][1]
    other_conn = "0x7e65d4f3-a" if first_conn == "0x17be5add-d" else "0x17be5add-d"
    
    print(f"   First request sent to connection: {first_conn}")
    
    print("\n2. First connection responds with NOTFOUND")
    print("-" * 70)
    print(f"   Connection {first_conn} doesn't have the transaction")
    
    await relay.on_tx_notfound(first_conn, [test_txid])
    await asyncio.sleep(0.01)
    
    print("\n3. Check if retry was sent to other connection")
    print("-" * 70)
    
    get_messages = [m for m in sent_messages if m[0] == "GET"]
    
    if len(get_messages) >= 2:
        second_conn = get_messages[1][1]
        print(f"   ✓ Retry sent to connection: {second_conn}")
        assert second_conn == other_conn, f"Expected {other_conn}, got {second_conn}"
        
        # Simulate the second connection responding with TX_DATA
        print("\n4. Second connection responds with TX_DATA")
        print("-" * 70)
        
        await relay.on_tx_data(other_conn, [{"txid": test_txid, "tx_bytes": test_tx_raw}])
        await asyncio.sleep(0.01)
        
        if admitted_txs:
            print(f"   ✓ SUCCESS: Transaction admitted to mempool!")
            print(f"   From peer node: 0xb11a50ed93 (connection {other_conn})")
        else:
            print(f"   ✗ FAILURE: Transaction was NOT admitted")
    else:
        print(f"   ✗ FAILURE: No retry sent (only {len(get_messages)} GET messages)")
        print("   This is the bug: both connections lost the txid after first NOTFOUND")
    
    print("\n" + "=" * 70)
    print("Result:", "PASS ✓" if len(admitted_txs) > 0 else "FAIL ✗")
    print("=" * 70)
    
    if len(admitted_txs) > 0:
        print("\nThe fix resolves the issue:")
        print("  ✓ NOTFOUND only clears txid from responding connection")
        print("  ✓ Other connections from same peer are tried")
        print("  ✓ Transaction successfully fetched and admitted")
    else:
        print("\nThe bug still exists:")
        print("  ✗ NOTFOUND cleared txid from all connections")
        print("  ✗ No retry to other connection")
        print("  ✗ Transaction not fetched")


if __name__ == "__main__":
    asyncio.run(main())
