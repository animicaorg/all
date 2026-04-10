"""
Integration test to verify that dropped transactions are automatically retried
by the watchdog loop without requiring manual intervention.

This simulates the user's reported issue where transactions get stuck and
the watchdog should automatically recover.
"""
import asyncio
import hashlib
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_watchdog_can_retry_dropped_transactions():
    """
    Test that dropped transactions can be retried by the watchdog.
    
    This verifies the fix where dropped transactions had their next_retry_at
    stuck in the future, preventing automatic retry.
    """
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_raw_mock = AsyncMock(return_value=None)
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
    
    # Create service with short intervals for testing
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
        get_tx_raw=get_tx_raw_mock,
        admit_tx=admit_tx_mock,
        list_mempool_hashes=list_mempool_hashes_mock,
        # Short intervals for testing
        inflight_timeout_s=0.5,  # 500ms timeout
        request_cooldown_s=1.0,  # 1 second cooldown
        mempool_watchdog_interval_s=0.3,  # 300ms watchdog interval
    )
    
    # Create a test transaction ID
    txid = hashlib.sha256(b"test_transaction").digest()
    
    # Add peer with the transaction
    async with service._lock:
        state = service._ensure_peer("peer1")
        state.known_txids.add(txid)
    
    # First request - should succeed
    requested = await service.request_missing_known(limit=10, trigger="test")
    assert requested == 1, "Should request the transaction"
    
    # Verify transaction is in flight
    assert txid in service._inflight
    
    # Mark the transaction as dropped (simulating timeout or failure)
    now = time.time()
    service._clear_inflight(txid)
    service._request_mgr.mark_dropped(
        txid, peer="peer1", reason="fetch_timeout", now=now
    )
    
    # Verify transaction is no longer in flight
    assert txid not in service._inflight
    
    # Verify transaction state is "dropped_evicted"
    req_state = service._request_mgr.get_state(txid)
    assert req_state is not None
    assert req_state.state == "dropped_evicted"
    
    # KEY TEST: Verify that can_request returns True immediately after drop
    # This is the fix - before, next_retry_at would be stuck in the future
    assert service._request_mgr.can_request(txid, now=now), \
        "Dropped transaction should be immediately retryable"
    
    # Re-add to peer's known txids (peer still knows about it)
    async with service._lock:
        state = service._peer_state.get("peer1")
        if state and txid not in state.known_txids:
            state.known_txids.add(txid)
    
    # Now request again - this should succeed because next_retry_at was reset
    send_tx_get_mock.reset_mock()
    requested = await service.request_missing_known(limit=10, trigger="watchdog_test")
    assert requested == 1, "Should be able to re-request the dropped transaction"
    assert send_tx_get_mock.call_count == 1, "Should send GET request"
    
    print("✅ Watchdog automatic retry test passed!")


@pytest.mark.asyncio
async def test_multiple_drop_retry_cycles():
    """
    Test that a transaction can go through multiple request-drop-retry cycles.
    """
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_raw_mock = AsyncMock(return_value=None)
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
        get_tx_raw=get_tx_raw_mock,
        admit_tx=admit_tx_mock,
        list_mempool_hashes=list_mempool_hashes_mock,
        request_cooldown_s=1.0,
    )
    
    txid = hashlib.sha256(b"retry_test").digest()
    
    # Add peer with transaction
    async with service._lock:
        state = service._ensure_peer("peer1")
        state.known_txids.add(txid)
    
    # Perform 3 cycles of request -> drop -> verify retryable
    for cycle in range(3):
        # Request
        requested = await service.request_missing_known(limit=10)
        assert requested == 1, f"Cycle {cycle+1}: Should request transaction"
        
        # Drop
        service._clear_inflight(txid)
        service._request_mgr.mark_dropped(txid, peer="peer1", reason="timeout", now=time.time())
        
        # Verify immediately retryable
        assert service._request_mgr.can_request(txid, now=time.time()), \
            f"Cycle {cycle+1}: Dropped transaction should be immediately retryable"
        
        # Re-add to peer's known txids
        async with service._lock:
            state = service._peer_state.get("peer1")
            if state and txid not in state.known_txids:
                state.known_txids.add(txid)
    
    print("✅ Multiple retry cycles test passed!")


if __name__ == "__main__":
    asyncio.run(test_watchdog_can_retry_dropped_transactions())
    asyncio.run(test_multiple_drop_retry_cycles())
    print("✅ All integration tests passed!")
