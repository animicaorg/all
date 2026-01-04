"""
Test that transaction propagation recovers after timeout.

This test validates the fix for the issue where transactions that timeout
remain in known_txids, preventing re-announcement and propagation.
"""

import asyncio
import hashlib

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_timeout_clears_known_txids_for_retry():
    """
    Test that when a transaction fetch times out with no retry candidates,
    the txid is removed from the peer's known_txids so it can be announced again.
    
    This prevents the bug where:
    1. Node learns about tx from peer A
    2. Fetch times out
    3. Peer A tries to broadcast again but skips because tx is in known_txids
    4. Node never gets the transaction
    """
    sent_inv: list[tuple[str, list[bytes]]] = []
    sent_get: list[tuple[str, list[bytes]]] = []

    async def send_inv(peer: str, txids: list[bytes]):
        sent_inv.append((peer, list(txids)))

    async def send_get(peer: str, txids: list[bytes]):
        sent_get.append((peer, list(txids)))

    async def send_noop(_peer: str, _payload):
        return None

    async def has_tx(_txid: bytes) -> bool:
        return False

    async def has_chain_tx(_txid: bytes) -> bool:
        return False

    async def get_tx_raw(_txid: bytes):
        return None

    async def admit_tx(_raw: bytes, _origin: str | None):
        return False, "not_implemented"

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        inflight_timeout_s=0.1,
        inflight_max_retries=2,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-a"],
        peer_eligible=lambda _peer: True,
        send_tx_inv=send_inv,
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

    # Register peer
    relay.register_peer("peer-a", peer_node_id="node-a")

    # Create a transaction
    txid = hashlib.sha3_256(b"test-tx").digest()

    # Simulate peer-a announcing the tx via on_tx_inv
    # This will add it to known_txids and send tx_get
    await relay.on_tx_inv("peer-a", [txid])

    # Verify tx_get was sent
    assert len(sent_get) == 1
    assert sent_get[0] == ("peer-a", [txid])

    # Verify txid is in known_txids
    state = relay._peer_state.get("peer-a")
    assert state is not None
    assert txid in state.known_txids

    # Start the inflight timeout loop
    timeout_task = asyncio.create_task(relay.inflight_timeout_loop())

    try:
        # Wait for timeout (0.1s) + loop check interval (0.5s) + buffer
        await asyncio.sleep(0.8)

        # After timeout, txid should be removed from inflight
        assert txid not in relay._inflight

        # And it should be removed from known_txids (the fix!)
        assert txid not in state.known_txids, (
            "Transaction should be removed from known_txids after timeout "
            "with no retry candidates"
        )

        # Now simulate peer-a trying to broadcast again
        # Before the fix, this would skip because tx was in known_txids
        # After the fix, it should add to inv_queue
        await relay._broadcast_inv([txid], exclude_peer=None)

        # Verify it was added to inv_queue
        assert len(state.inv_queue) > 0
        assert txid in state.inv_queue

        # Start inv flush loop to send the inv
        inv_task = asyncio.create_task(relay.inv_flush_loop())

        try:
            # Wait for inv to be sent
            await asyncio.sleep(0.2)

            # Verify tx_inv was sent (re-announcement worked!)
            inv_msgs = [(peer, txids) for peer, txids in sent_inv if txid in txids]
            assert len(inv_msgs) > 0, "Transaction should be re-announced after timeout"

        finally:
            relay._running = False
            inv_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await inv_task

    finally:
        relay._running = False
        timeout_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await timeout_task


@pytest.mark.asyncio
async def test_notfound_clears_known_txids():
    """
    Test that when a peer responds with tx_notfound, the txid is removed
    from their known_txids.
    """
    async def send_noop(_peer: str, _payload):
        return None

    async def has_tx(_txid: bytes) -> bool:
        return False

    async def has_chain_tx(_txid: bytes) -> bool:
        return False

    async def get_tx_raw(_txid: bytes):
        return None

    async def admit_tx(_raw: bytes, _origin: str | None):
        return False, "not_implemented"

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        peer_ids=lambda: ["peer-a"],
        peer_eligible=lambda _peer: True,
        send_tx_inv=send_noop,
        send_tx_get=send_noop,
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

    relay.register_peer("peer-a", peer_node_id="node-a")

    txid = hashlib.sha3_256(b"notfound-tx").digest()

    # Simulate learning about tx
    await relay.on_tx_inv("peer-a", [txid])

    # Verify it's in known_txids
    state = relay._peer_state.get("peer-a")
    assert state is not None
    assert txid in state.known_txids

    # Simulate peer responding with notfound
    await relay.on_tx_notfound("peer-a", [txid])

    # Verify it's removed from known_txids
    assert txid not in state.known_txids, (
        "Transaction should be removed from known_txids when peer responds with notfound"
    )
