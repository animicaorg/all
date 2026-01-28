"""
Test that mempool_sync_loop calls request_missing_known periodically.

This verifies the fix for the issue where mempool shows as empty even though
peers know about transactions. The fix adds periodic calls to request_missing_known()
to fetch transactions that peers have advertised but haven't been delivered yet.
"""
import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_mempool_sync_loop_requests_missing_known():
    """Test that mempool_sync_loop periodically calls request_missing_known."""
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_mock = AsyncMock(return_value=None)
    admit_tx_mock = AsyncMock(return_value=(True, None))
    list_mempool_hashes_mock = AsyncMock(return_value=[])
    
    peer_ids_mock = MagicMock(return_value=[])
    peer_eligible_mock = MagicMock(return_value=True)
    
    send_tx_inv_mock = AsyncMock()
    send_tx_get_mock = AsyncMock()
    send_tx_data_mock = AsyncMock()
    send_tx_notfound_mock = AsyncMock()
    send_mempool_req_mock = AsyncMock()
    send_mempool_resp_mock = AsyncMock()
    
    # Create service with short intervals for testing
    service = TxRelayService(
        max_tx_bytes=1_000_000,
        inv_flush_interval_s=1.0,
        mempool_sync_interval_s=2.0,  # Short interval for testing
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
    
    # Add a peer with known txids
    conn_id = "test-conn-123"
    tx1 = hashlib.sha3_256(b"tx1").digest()
    tx2 = hashlib.sha3_256(b"tx2").digest()
    
    # Simulate peer announcing txids
    await service.on_tx_inv(conn_id, [tx1, tx2])
    
    # Verify txids are in peer's known_txids
    async with service._lock:
        state = service._peer_state.get(conn_id)
        assert state is not None
        assert len(state.known_txids) == 2
    
    # Mock request_missing_known to track if it's called
    original_request_missing_known = service.request_missing_known
    call_count = 0
    
    async def tracked_request_missing_known(limit=128, trigger="test"):
        nonlocal call_count
        call_count += 1
        return await original_request_missing_known(limit=limit, trigger=trigger)
    
    service.request_missing_known = tracked_request_missing_known
    
    # Start mempool_sync_loop
    loop_task = asyncio.create_task(service.mempool_sync_loop())
    
    # Wait for at least one cycle (mempool_sync_interval_s = 2.0)
    await asyncio.sleep(3.0)
    
    # Stop the loop
    service._running = False
    await asyncio.sleep(0.5)
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass
    
    # Verify request_missing_known was called at least once
    assert call_count >= 1, f"request_missing_known should be called at least once, got {call_count}"
    
    # Verify send_tx_get was called to fetch the missing transactions
    assert send_tx_get_mock.call_count >= 1, "send_tx_get should be called to fetch missing txs"


@pytest.mark.asyncio
async def test_request_missing_known_fetches_peer_txids():
    """Test that request_missing_known requests txs from peers that weren't fetched."""
    
    # Mock dependencies
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
    
    # Add a peer with known txids by directly manipulating the state
    # (simulating a scenario where peer reported having txs but never sent them)
    conn_id = "peer1"
    tx1 = hashlib.sha3_256(b"tx1").digest()
    tx2 = hashlib.sha3_256(b"tx2").digest()
    tx3 = hashlib.sha3_256(b"tx3").digest()
    
    # Directly add to peer's known_txids without triggering on_tx_inv
    # This simulates the scenario where we learned about txids but haven't fetched them
    async with service._lock:
        state = service._ensure_peer(conn_id)
        state.known_txids.add(tx1)
        state.known_txids.add(tx2)
        state.known_txids.add(tx3)
    
    # Now call request_missing_known with explicit trigger
    requested = await service.request_missing_known(limit=10, trigger="test")
    
    # Should have requested the 3 transactions
    assert requested == 3, f"Expected 3 txs requested, got {requested}"
    assert send_tx_get_mock.call_count == 1, "send_tx_get should be called once"
    
    # Verify the correct txids were requested
    call_args = send_tx_get_mock.call_args
    assert call_args is not None
    requested_conn_id = call_args[0][0]
    requested_txids = call_args[0][1]
    
    assert requested_conn_id == conn_id
    assert len(requested_txids) == 3
    assert set(requested_txids) == {tx1, tx2, tx3}


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
