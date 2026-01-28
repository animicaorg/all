#!/usr/bin/env python3
"""
Verification script for mempool sync fix.

This script demonstrates that the mempool_sync_loop now automatically
fetches transactions that peers know about, fixing the issue where
the mempool could be empty even though peers reported having transactions.
"""
import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

from p2p.txrelay import TxRelayService


async def verify_automatic_fetch():
    """Verify that the mempool sync loop automatically fetches peer-known txs."""
    
    print("\n" + "="*70)
    print("Mempool Sync Fix Verification")
    print("="*70)
    print("\nScenario: Peer reports having transactions but mempool is empty")
    print("Expected: Mempool sync loop automatically fetches missing transactions\n")
    
    # Create mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_mock = AsyncMock(return_value=None)
    admit_tx_mock = AsyncMock(return_value=(True, None))
    list_mempool_hashes_mock = AsyncMock(return_value=[])
    
    peer_ids_mock = MagicMock(return_value=["peer1"])
    peer_eligible_mock = MagicMock(return_value=True)
    
    send_tx_inv_mock = AsyncMock()
    send_tx_get_mock = AsyncMock()
    send_tx_data_mock = AsyncMock()
    send_tx_notfound_mock = AsyncMock()
    send_mempool_req_mock = AsyncMock()
    send_mempool_resp_mock = AsyncMock()
    
    # Create service with very short interval for quick verification
    service = TxRelayService(
        max_tx_bytes=1_000_000,
        inv_flush_interval_s=1.0,
        mempool_sync_interval_s=2.0,  # 2 seconds for quick testing
        peer_ids=peer_ids_mock,
        peer_eligible=peer_eligible_mock,
        send_tx_inv=send_tx_inv_mock,
        send_tx_get=send_tx_get_mock,
        send_tx_data=send_tx_data_mock,
        send_tx_notfound=send_tx_notfound_mock,
        send_mempool_req=send_mempool_req_mock,
        send_mempool_resp=send_mempool_resp_mock,
        has_tx=has_tx_mock,
        has_chain_tx=has_chain_tx_mock,
        get_tx_raw=get_tx_mock,
        admit_tx=admit_tx_mock,
        list_mempool_hashes=list_mempool_hashes_mock,
    )
    
    # Simulate peer having transactions
    conn_id = "peer1"
    tx1 = hashlib.sha3_256(b"transaction1").digest()
    tx2 = hashlib.sha3_256(b"transaction2").digest()
    tx3 = hashlib.sha3_256(b"transaction3").digest()
    
    print(f"Step 1: Simulating peer '{conn_id}' reporting 3 transactions...")
    print(f"  - tx1: {tx1.hex()[:16]}...")
    print(f"  - tx2: {tx2.hex()[:16]}...")
    print(f"  - tx3: {tx3.hex()[:16]}...")
    
    # Directly add to peer's known_txids (simulating INV received but not fetched)
    async with service._lock:
        state = service._ensure_peer(conn_id)
        state.known_txids.add(tx1)
        state.known_txids.add(tx2)
        state.known_txids.add(tx3)
    
    print("\nStep 2: Peer's known_txids contains 3 transactions")
    print("        (simulating scenario where INV was received but not fetched)")
    
    # Start mempool sync loop
    print("\nStep 3: Starting mempool_sync_loop...")
    loop_task = asyncio.create_task(service.mempool_sync_loop())
    
    # Wait for at least one sync cycle
    print("        Waiting for automatic fetch (2-3 seconds)...")
    await asyncio.sleep(3.5)
    
    # Stop the loop
    service._running = False
    await asyncio.sleep(0.5)
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass
    
    print("\nStep 4: Checking if transactions were automatically fetched...")
    
    # Verify send_tx_get was called
    if send_tx_get_mock.call_count > 0:
        print(f"✓ SUCCESS: send_tx_get was called {send_tx_get_mock.call_count} time(s)")
        
        # Check what was requested
        call_args = send_tx_get_mock.call_args
        if call_args:
            requested_conn_id = call_args[0][0]
            requested_txids = call_args[0][1]
            print(f"✓ Requested from peer: {requested_conn_id}")
            print(f"✓ Number of transactions requested: {len(requested_txids)}")
            
            if len(requested_txids) == 3:
                print("✓ All 3 missing transactions were requested")
            else:
                print(f"⚠ Only {len(requested_txids)} of 3 transactions requested")
    else:
        print("✗ FAILED: send_tx_get was never called")
        return False
    
    print("\n" + "="*70)
    print("VERIFICATION PASSED")
    print("="*70)
    print("\nThe fix successfully ensures that transactions peers know about")
    print("are automatically fetched, even if initial delivery failed.")
    print("\nThis resolves the issue where mempool appears empty despite")
    print("peers reporting they have transactions.")
    
    return True


if __name__ == "__main__":
    success = asyncio.run(verify_automatic_fetch())
    exit(0 if success else 1)
