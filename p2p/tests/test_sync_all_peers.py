"""
Test sync_all_peers functionality added to ensure miners sync all peer mempools
when building block templates.
"""
import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_sync_all_peers_sends_requests_to_all_eligible_peers():
    """Test that sync_all_peers sends mempool sync requests to all eligible peers."""
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_mock = AsyncMock(return_value=None)
    admit_tx_mock = AsyncMock(return_value=(True, None))
    list_mempool_hashes_mock = AsyncMock(return_value=[])
    
    peer_ids_mock = MagicMock(return_value=["peer1", "peer2", "peer3"])
    peer_eligible_mock = MagicMock(return_value=True)
    
    send_tx_inv_mock = AsyncMock()
    send_tx_get_mock = AsyncMock()
    send_tx_data_mock = AsyncMock()
    send_tx_notfound_mock = AsyncMock()
    send_mempool_req_mock = AsyncMock()
    send_mempool_resp_mock = AsyncMock()
    
    # Create service
    service = TxRelayService(
        max_tx_bytes=1_000_000,
        mempool_sync_limit=2000,
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
    
    # Register peers (not async, no await)
    service.register_peer("peer1", peer_node_id="node1", direction="outbound", remote="127.0.0.1:30334")
    service.register_peer("peer2", peer_node_id="node2", direction="outbound", remote="127.0.0.1:30335")
    service.register_peer("peer3", peer_node_id="node3", direction="outbound", remote="127.0.0.1:30336")
    
    # Call sync_all_peers with minimal timeout
    synced_count = await service.sync_all_peers(timeout_s=0.1)
    
    # Should have synced 3 peers
    assert synced_count == 3, f"Expected 3 peers synced, got {synced_count}"
    
    # Should have sent 3 mempool requests
    assert send_mempool_req_mock.call_count == 3, \
        f"Expected 3 mempool requests, got {send_mempool_req_mock.call_count}"
    
    # Verify each peer got a request
    call_peer_ids = [call[0][0] for call in send_mempool_req_mock.call_args_list]
    assert "peer1" in call_peer_ids
    assert "peer2" in call_peer_ids
    assert "peer3" in call_peer_ids


@pytest.mark.asyncio
async def test_sync_all_peers_skips_ineligible_peers():
    """Test that sync_all_peers only syncs eligible peers."""
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_mock = AsyncMock(return_value=None)
    admit_tx_mock = AsyncMock(return_value=(True, None))
    list_mempool_hashes_mock = AsyncMock(return_value=[])
    
    # peer_eligible returns False for peer2 and peer3
    def peer_eligible_side_effect(peer_id):
        return peer_id == "peer1"
    
    peer_ids_mock = MagicMock(return_value=["peer1", "peer2", "peer3"])
    peer_eligible_mock = MagicMock(side_effect=peer_eligible_side_effect)
    
    send_tx_inv_mock = AsyncMock()
    send_tx_get_mock = AsyncMock()
    send_tx_data_mock = AsyncMock()
    send_tx_notfound_mock = AsyncMock()
    send_mempool_req_mock = AsyncMock()
    send_mempool_resp_mock = AsyncMock()
    
    # Create service
    service = TxRelayService(
        max_tx_bytes=1_000_000,
        mempool_sync_limit=2000,
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
    
    # Register peers (not async, no await)
    service.register_peer("peer1", peer_node_id="node1", direction="outbound", remote="127.0.0.1:30334")
    service.register_peer("peer2", peer_node_id="node2", direction="outbound", remote="127.0.0.1:30335")
    service.register_peer("peer3", peer_node_id="node3", direction="outbound", remote="127.0.0.1:30336")
    
    # Call sync_all_peers with minimal timeout
    synced_count = await service.sync_all_peers(timeout_s=0.1)
    
    # Should have synced only 1 eligible peer
    assert synced_count == 1, f"Expected 1 peer synced, got {synced_count}"
    
    # Should have sent 1 mempool request (only to peer1)
    assert send_mempool_req_mock.call_count == 1, \
        f"Expected 1 mempool request, got {send_mempool_req_mock.call_count}"
    
    # Verify only peer1 got a request
    call_peer_ids = [call[0][0] for call in send_mempool_req_mock.call_args_list]
    assert call_peer_ids == ["peer1"]


@pytest.mark.asyncio
async def test_sync_all_peers_returns_zero_with_no_peers():
    """Test that sync_all_peers returns 0 when there are no peers."""
    
    # Mock dependencies
    has_tx_mock = AsyncMock(return_value=False)
    has_chain_tx_mock = AsyncMock(return_value=False)
    get_tx_mock = AsyncMock(return_value=None)
    admit_tx_mock = AsyncMock(return_value=(True, None))
    list_mempool_hashes_mock = AsyncMock(return_value=[])
    
    peer_ids_mock = MagicMock(return_value=[])  # No peers
    peer_eligible_mock = MagicMock(return_value=True)
    
    send_tx_inv_mock = AsyncMock()
    send_tx_get_mock = AsyncMock()
    send_tx_data_mock = AsyncMock()
    send_tx_notfound_mock = AsyncMock()
    send_mempool_req_mock = AsyncMock()
    send_mempool_resp_mock = AsyncMock()
    
    # Create service
    service = TxRelayService(
        max_tx_bytes=1_000_000,
        mempool_sync_limit=2000,
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
    
    # Call sync_all_peers with minimal timeout
    synced_count = await service.sync_all_peers(timeout_s=0.1)
    
    # Should have synced 0 peers
    assert synced_count == 0, f"Expected 0 peers synced, got {synced_count}"
    
    # Should not have sent any mempool requests
    assert send_mempool_req_mock.call_count == 0, \
        f"Expected 0 mempool requests, got {send_mempool_req_mock.call_count}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
