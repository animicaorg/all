"""
Test that stale "accepted_in_mempool" state is cleared when transaction is not in mempool.

This test verifies the fix for the issue where peers report having known txids
but the local mempool is empty. The root cause was that transactions marked as
"accepted_in_mempool" were never re-requested, even if they were later evicted.
"""
import asyncio
import hashlib
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_stale_accepted_state_is_cleared():
    """Test that stale accepted_in_mempool state is cleared and tx is re-requested."""
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)  # Transaction NOT in mempool
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
    
    # Setup: Create a transaction and mark it as accepted
    conn_id = "peer1"
    tx1 = hashlib.sha3_256(b"tx1").digest()
    
    # Add to peer's known_txids
    async with service._lock:
        state = service._ensure_peer(conn_id)
        state.known_txids.add(tx1)
    
    # Manually mark as accepted in mempool (simulating a previous successful fetch)
    now = time.time()
    service._request_mgr.mark_accepted(tx1, peer="peer1", now=now)
    
    # Verify the state is "accepted_in_mempool"
    req_state = service._request_mgr.get_state(tx1)
    assert req_state is not None
    assert req_state.state == "accepted_in_mempool"
    
    # At this point, can_request() would return False
    assert not service._request_mgr.can_request(tx1, now=now)
    
    # Now call request_missing_known - it should detect the stale state and clear it
    requested = await service.request_missing_known(limit=10, trigger="test")
    
    # Should have requested 1 transaction (after clearing the stale state)
    assert requested == 1, f"Expected 1 tx requested, got {requested}"
    assert send_tx_get_mock.call_count == 1
    
    # Verify the stale state was cleared and tx was re-requested
    call_args = send_tx_get_mock.call_args
    assert call_args is not None
    requested_conn_id = call_args[0][0]
    requested_txids = call_args[0][1]
    
    assert requested_conn_id == conn_id
    assert len(requested_txids) == 1
    assert requested_txids[0] == tx1


@pytest.mark.asyncio
async def test_accepted_state_preserved_if_tx_in_mempool():
    """Test that accepted state is NOT cleared if transaction is actually in mempool."""
    
    # Mock dependencies - transaction IS in mempool
    has_tx_mock = AsyncMock(return_value=True)  # Transaction IS in mempool
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
    
    # Setup: Create a transaction and mark it as accepted
    conn_id = "peer1"
    tx1 = hashlib.sha3_256(b"tx1").digest()
    
    # Add to peer's known_txids
    async with service._lock:
        state = service._ensure_peer(conn_id)
        state.known_txids.add(tx1)
    
    # Manually mark as accepted in mempool
    now = time.time()
    service._request_mgr.mark_accepted(tx1, peer="peer1", now=now)
    
    # Now call request_missing_known - should skip because tx IS in mempool
    requested = await service.request_missing_known(limit=10, trigger="test")
    
    # Should NOT have requested anything (tx is in mempool)
    assert requested == 0, f"Expected 0 tx requested, got {requested}"
    assert send_tx_get_mock.call_count == 0
    
    # Verify the state was NOT cleared (tx is actually in mempool)
    req_state = service._request_mgr.get_state(tx1)
    assert req_state is not None
    assert req_state.state == "accepted_in_mempool"


@pytest.mark.asyncio
async def test_watchdog_fetches_after_clearing_stale_state():
    """Test that the watchdog loop can fetch transactions after clearing stale state."""
    
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
    
    # Create service with short watchdog interval for testing
    service = TxRelayService(
        max_tx_bytes=1_000_000,
        mempool_watchdog_interval_s=0.5,  # Fast for testing
        mempool_watchdog_limit=10,
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
    
    # Setup: Add peer with known txids and stale accepted state
    conn_id = "peer1"
    tx1 = hashlib.sha3_256(b"tx1").digest()
    
    async with service._lock:
        state = service._ensure_peer(conn_id)
        state.known_txids.add(tx1)
    
    # Mark as accepted (stale state)
    now = time.time()
    service._request_mgr.mark_accepted(tx1, peer="peer1", now=now)
    
    # Start watchdog loop
    watchdog_task = asyncio.create_task(service.mempool_watchdog_loop())
    
    # Wait for watchdog to run at least once
    await asyncio.sleep(1.0)
    
    # Stop the loop
    service._running = False
    await asyncio.sleep(0.2)
    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass
    
    # Verify TX_GET was called (watchdog cleared stale state and fetched)
    assert send_tx_get_mock.call_count >= 1, "Watchdog should have fetched the transaction"


@pytest.mark.asyncio
async def test_force_request_bypasses_retry_guards():
    """Manual import should re-request tx even when cooldown/inflight/reject guards are set."""

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
        inflight_timeout_s=30.0,
        request_cooldown_s=30.0,
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

    conn_id = "peer1"
    tx1 = hashlib.sha3_256(b"tx-force").digest()

    async with service._lock:
        state = service._ensure_peer(conn_id)
        state.known_txids.add(tx1)

    now = time.time()
    service._request_mgr.mark_requested(tx1, peer=conn_id, now=now)
    service._set_inflight(tx1, conn_id=conn_id, peer_node_id=None, now=now, attempts=1)
    service._reject_cache[tx1] = now + 60.0

    requested = await service.request_missing_known(limit=10, trigger="test_no_force", force=False)
    assert requested == 0

    requested = await service.request_missing_known(limit=10, trigger="test_force", force=True)
    assert requested == 1
    assert send_tx_get_mock.call_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
