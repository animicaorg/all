#!/usr/bin/env python3
"""
Verification script for the mempool peer eligibility fix.

This script demonstrates that request_missing_known now properly filters
ineligible peers before processing their known_txids.
"""
import asyncio
import hashlib
import sys
from unittest.mock import AsyncMock, MagicMock

from p2p.txrelay import TxRelayService


async def verify_fix():
    """Verify that the fix correctly filters ineligible peers."""
    
    print("=" * 70)
    print("Mempool Peer Eligibility Fix Verification")
    print("=" * 70)
    print()
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_mock = AsyncMock(return_value=None)
    admit_tx_mock = AsyncMock(return_value=(True, None))
    list_mempool_hashes_mock = AsyncMock(return_value=[])
    
    peer_ids_mock = MagicMock(return_value=["peer1", "peer2", "peer3"])
    
    # Simulate the issue: peer1 and peer2 are duplicate connections to same node
    # Only peer3 is eligible
    eligible_peers = {"peer3"}
    
    def peer_eligible_fn(peer_key: str) -> bool:
        is_eligible = peer_key in eligible_peers
        print(f"  Checking peer_eligible({peer_key}): {is_eligible}")
        return is_eligible
    
    peer_eligible_mock = MagicMock(side_effect=peer_eligible_fn)
    
    send_tx_inv_mock = AsyncMock()
    send_tx_get_mock = AsyncMock()
    send_tx_data_mock = AsyncMock()
    send_tx_notfound_mock = AsyncMock()
    send_mempool_req_mock = AsyncMock()
    send_mempool_resp_mock = AsyncMock()
    
    service = TxRelayService(
        max_tx_bytes=1_000_000,
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
    
    # Create transaction hashes
    tx1 = hashlib.sha3_256(b"tx1").digest()
    tx2 = hashlib.sha3_256(b"tx2").digest()
    tx3 = hashlib.sha3_256(b"tx3").digest()
    
    print("Setting up test scenario:")
    print("  - peer1: INELIGIBLE (duplicate connection), has tx1")
    print("  - peer2: INELIGIBLE (duplicate connection), has tx2")
    print("  - peer3: ELIGIBLE (active connection), has tx3")
    print()
    
    # Add peers with known txids
    async with service._lock:
        state1 = service._ensure_peer("peer1")
        state1.known_txids.add(tx1)
        
        state2 = service._ensure_peer("peer2")
        state2.known_txids.add(tx2)
        
        state3 = service._ensure_peer("peer3")
        state3.known_txids.add(tx3)
    
    print("Calling request_missing_known()...")
    print()
    
    # Call request_missing_known
    requested = await service.request_missing_known(limit=10, trigger="verification")
    
    print()
    print("Results:")
    print(f"  Transactions requested: {requested}")
    print(f"  send_tx_get call count: {send_tx_get_mock.call_count}")
    print()
    
    if requested == 1 and send_tx_get_mock.call_count == 1:
        call_args = send_tx_get_mock.call_args
        requested_conn_id = call_args[0][0]
        requested_txids = call_args[0][1]
        
        print(f"  ✓ Requested from peer: {requested_conn_id}")
        print(f"  ✓ Transaction hash: 0x{requested_txids[0].hex()}")
        print()
        
        if requested_conn_id == "peer3" and requested_txids[0] == tx3:
            print("=" * 70)
            print("✓ FIX VERIFIED: Only eligible peer was processed!")
            print("=" * 70)
            print()
            print("Explanation:")
            print("  - peer1 and peer2 were SKIPPED (ineligible)")
            print("  - peer3 was PROCESSED (eligible)")
            print("  - Transaction from peer3 was requested")
            print("  - NO requests sent to ineligible peers")
            print()
            return True
        else:
            print("❌ ERROR: Wrong peer or transaction was processed")
            return False
    else:
        print("❌ ERROR: Expected 1 transaction from 1 eligible peer")
        return False


async def main():
    """Run verification."""
    success = await verify_fix()
    
    if success:
        print("=" * 70)
        print("Verification Complete: Fix is working correctly!")
        print("=" * 70)
        return 0
    else:
        print("=" * 70)
        print("Verification Failed: Fix may not be working correctly")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
