"""
Test that request_missing_known only processes eligible peers.

This verifies the fix for the issue where ineligible peers (duplicate connections,
disconnected peers, etc.) were being processed in request_missing_known, causing
transaction requests to fail or be ignored.
"""
import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_request_missing_known_skips_ineligible_peers():
    """Test that request_missing_known skips ineligible peers."""
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_mock = AsyncMock(return_value=None)
    admit_tx_mock = AsyncMock(return_value=(True, None))
    list_mempool_hashes_mock = AsyncMock(return_value=[])
    
    peer_ids_mock = MagicMock(return_value=["eligible_peer", "ineligible_peer"])
    
    # peer_eligible returns True only for "eligible_peer"
    def peer_eligible_fn(peer_key: str) -> bool:
        return peer_key == "eligible_peer"
    
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
    tx4 = hashlib.sha3_256(b"tx4").digest()
    
    # Add both eligible and ineligible peers with known txids
    async with service._lock:
        # Eligible peer has tx1 and tx2
        eligible_state = service._ensure_peer("eligible_peer")
        eligible_state.known_txids.add(tx1)
        eligible_state.known_txids.add(tx2)
        
        # Ineligible peer has tx3 and tx4
        ineligible_state = service._ensure_peer("ineligible_peer")
        ineligible_state.known_txids.add(tx3)
        ineligible_state.known_txids.add(tx4)
    
    # Call request_missing_known
    requested = await service.request_missing_known(limit=10, trigger="test")
    
    # Should have requested only 2 transactions (from eligible peer)
    assert requested == 2, f"Expected 2 txs requested from eligible peer, got {requested}"
    
    # Verify send_tx_get was called exactly once (for eligible peer only)
    assert send_tx_get_mock.call_count == 1, "send_tx_get should be called once for eligible peer"
    
    # Verify the correct txids were requested (only from eligible peer)
    call_args = send_tx_get_mock.call_args
    assert call_args is not None
    requested_conn_id = call_args[0][0]
    requested_txids = call_args[0][1]
    
    assert requested_conn_id == "eligible_peer"
    assert len(requested_txids) == 2
    # Should be tx1 and tx2 (from eligible peer), not tx3 and tx4 (from ineligible peer)
    assert set(requested_txids) == {tx1, tx2}
    
    # Verify peer_eligible was called to check eligibility
    assert peer_eligible_mock.call_count >= 2, "peer_eligible should be called at least twice"


@pytest.mark.asyncio
async def test_request_missing_known_with_no_eligible_peers():
    """Test that request_missing_known handles case where no peers are eligible."""
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_mock = AsyncMock(return_value=None)
    admit_tx_mock = AsyncMock(return_value=(True, None))
    list_mempool_hashes_mock = AsyncMock(return_value=[])
    
    peer_ids_mock = MagicMock(return_value=["peer1", "peer2"])
    # All peers are ineligible
    peer_eligible_mock = MagicMock(return_value=False)
    
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
    
    # Add ineligible peers with known txids
    async with service._lock:
        state1 = service._ensure_peer("peer1")
        state1.known_txids.add(tx1)
        
        state2 = service._ensure_peer("peer2")
        state2.known_txids.add(tx2)
    
    # Call request_missing_known
    requested = await service.request_missing_known(limit=10, trigger="test")
    
    # Should have requested 0 transactions (no eligible peers)
    assert requested == 0, f"Expected 0 txs requested (no eligible peers), got {requested}"
    
    # Verify send_tx_get was never called
    assert send_tx_get_mock.call_count == 0, "send_tx_get should not be called when no peers are eligible"


@pytest.mark.asyncio
async def test_request_missing_known_with_mixed_eligibility():
    """Test that request_missing_known handles multiple peers with mixed eligibility."""
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_mock = AsyncMock(return_value=None)
    admit_tx_mock = AsyncMock(return_value=(True, None))
    list_mempool_hashes_mock = AsyncMock(return_value=[])
    
    peer_ids_mock = MagicMock(return_value=["peer1", "peer2", "peer3"])
    
    # Only peer1 and peer3 are eligible
    def peer_eligible_fn(peer_key: str) -> bool:
        return peer_key in ("peer1", "peer3")
    
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
    
    # Add peers with known txids
    async with service._lock:
        state1 = service._ensure_peer("peer1")
        state1.known_txids.add(tx1)
        
        state2 = service._ensure_peer("peer2")  # Ineligible
        state2.known_txids.add(tx2)
        
        state3 = service._ensure_peer("peer3")
        state3.known_txids.add(tx3)
    
    # Call request_missing_known
    requested = await service.request_missing_known(limit=10, trigger="test")
    
    # Should have requested 2 transactions (from peer1 and peer3, skipping peer2)
    assert requested == 2, f"Expected 2 txs requested from eligible peers, got {requested}"
    
    # Verify send_tx_get was called twice (once for each eligible peer)
    assert send_tx_get_mock.call_count == 2, "send_tx_get should be called twice for two eligible peers"
    
    # Verify the correct txids were requested
    all_requested_txids = []
    for call in send_tx_get_mock.call_args_list:
        requested_conn_id = call[0][0]
        requested_txids = call[0][1]
        # Should be peer1 or peer3, not peer2
        assert requested_conn_id in ("peer1", "peer3")
        all_requested_txids.extend(requested_txids)
    
    # Should have tx1 and tx3 (from eligible peers), not tx2 (from ineligible peer)
    assert set(all_requested_txids) == {tx1, tx3}
