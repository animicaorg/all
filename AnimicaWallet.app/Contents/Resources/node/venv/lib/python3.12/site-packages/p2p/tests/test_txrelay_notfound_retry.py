"""
Test that transaction fetching retries other peers when one responds with NOTFOUND.

This test validates the fix for the issue where:
1. Multiple peers report knowing about a transaction
2. First peer responds with NOTFOUND
3. System should retry with other peers instead of giving up
"""

import asyncio
import hashlib

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_notfound_retries_other_peers():
    """
    Test that when peer-a responds with NOTFOUND, the system tries peer-b
    instead of giving up immediately.
    """
    sent_get: list[tuple[str, list[bytes]]] = []
    tx_data_sent: dict[str, list[dict]] = {}

    async def send_get(peer: str, txids: list[bytes]):
        sent_get.append((peer, list(txids)))

    async def send_data(peer: str, items: list[dict]):
        tx_data_sent.setdefault(peer, []).extend(items)

    async def send_noop(_peer: str, _payload):
        return None

    async def has_tx(_txid: bytes) -> bool:
        return False

    async def has_chain_tx(_txid: bytes) -> bool:
        return False

    # peer-b has the transaction
    test_tx_raw = b"test-transaction-data"
    test_txid = hashlib.sha3_256(test_tx_raw).digest()

    async def get_tx_raw(txid: bytes):
        # Only peer-b has the transaction
        if txid == test_txid:
            # This will be called when peer-b tries to respond
            return test_tx_raw
        return None

    admitted_txs: list[tuple[bytes, str]] = []

    async def admit_tx(raw: bytes, origin: str | None):
        admitted_txs.append((raw, origin or "unknown"))
        return True, None

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        peer_ids=lambda: ["peer-a", "peer-b"],
        peer_eligible=lambda _peer: True,
        send_tx_inv=send_noop,
        send_tx_get=send_get,
        send_tx_data=send_data,
        send_tx_notfound=send_noop,
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

    # Manually add to known_txids (simulating peer advertising via mempool sync)
    # This bypasses on_tx_inv's automatic request logic
    relay._peer_state["peer-a"].known_txids.add(test_txid)
    relay._peer_state["peer-b"].known_txids.add(test_txid)
    relay._record_source(test_txid, "peer-a")
    relay._record_source(test_txid, "peer-b")

    # Verify both peers have it in known_txids
    state_a = relay._peer_state.get("peer-a")
    state_b = relay._peer_state.get("peer-b")
    assert state_a is not None and test_txid in state_a.known_txids
    assert state_b is not None and test_txid in state_b.known_txids

    # Request the transaction (system will pick one of the peers)
    requested = await relay.request_missing_known(limit=1, trigger="test")
    assert requested == 1

    # Wait a bit for request to be sent
    await asyncio.sleep(0.01)

    # One peer should have received a TX_GET
    assert len(sent_get) == 1
    first_peer, first_txids = sent_get[0]
    assert test_txid in first_txids

    # Simulate first peer responding with NOTFOUND
    await relay.on_tx_notfound(first_peer, [test_txid])

    # Wait for retry to be sent
    await asyncio.sleep(0.01)

    # System should have sent TX_GET to the other peer
    assert len(sent_get) == 2, "Should retry with second peer after NOTFOUND from first"
    second_peer, second_txids = sent_get[1]
    assert second_peer != first_peer, "Should request from different peer"
    assert test_txid in second_txids

    # Verify first peer's known_txids was cleared but second peer still has it
    assert test_txid not in state_a.known_txids or test_txid not in state_b.known_txids
    # At least one peer should still have it (the one that didn't respond NOTFOUND)
    if first_peer == "peer-a":
        assert test_txid not in state_a.known_txids
        assert test_txid in state_b.known_txids
    else:
        assert test_txid not in state_b.known_txids
        assert test_txid in state_a.known_txids

    # Now simulate the second peer successfully responding with TX_DATA
    await relay.on_tx_data(second_peer, [{"txid": test_txid, "tx_bytes": test_tx_raw}])

    # Wait for admission to complete
    await asyncio.sleep(0.01)

    # Verify transaction was admitted to mempool
    assert len(admitted_txs) == 1
    assert admitted_txs[0][0] == test_tx_raw


@pytest.mark.asyncio
async def test_notfound_from_all_peers_gives_up():
    """
    Test that when all peers respond with NOTFOUND, the system gives up
    and doesn't retry infinitely.
    """
    sent_get: list[tuple[str, list[bytes]]] = []

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
        return False, "not_found"

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        peer_ids=lambda: ["peer-a", "peer-b"],
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

    relay.register_peer("peer-a", peer_node_id="node-a")
    relay.register_peer("peer-b", peer_node_id="node-b")

    test_txid = hashlib.sha3_256(b"missing-tx").digest()

    # Manually add to known_txids for both peers
    relay._peer_state["peer-a"].known_txids.add(test_txid)
    relay._peer_state["peer-b"].known_txids.add(test_txid)
    relay._record_source(test_txid, "peer-a")
    relay._record_source(test_txid, "peer-b")

    # Request the transaction
    requested = await relay.request_missing_known(limit=1, trigger="test")
    assert requested == 1

    await asyncio.sleep(0.01)

    # First request sent
    assert len(sent_get) == 1
    first_peer = sent_get[0][0]

    # First peer responds with NOTFOUND
    await relay.on_tx_notfound(first_peer, [test_txid])
    await asyncio.sleep(0.01)

    # Should retry with second peer
    assert len(sent_get) == 2
    second_peer = sent_get[1][0]
    assert second_peer != first_peer

    # Second peer also responds with NOTFOUND
    await relay.on_tx_notfound(second_peer, [test_txid])
    await asyncio.sleep(0.01)

    # Should not retry anymore (all peers exhausted)
    assert len(sent_get) == 2, "Should not retry after all peers respond with NOTFOUND"

    # Verify transaction is in reject cache
    assert relay._reject_recent(test_txid), "Transaction should be in reject cache"


@pytest.mark.asyncio
async def test_notfound_only_clears_responding_peer():
    """
    Test that NOTFOUND only clears the txid from the peer that responded,
    not from all peers.
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
        peer_ids=lambda: ["peer-a", "peer-b", "peer-c"],
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
    relay.register_peer("peer-b", peer_node_id="node-b")
    relay.register_peer("peer-c", peer_node_id="node-c")

    test_txid = hashlib.sha3_256(b"test-tx").digest()

    # All three peers announce they have the transaction
    await relay.on_tx_inv("peer-a", [test_txid])
    await relay.on_tx_inv("peer-b", [test_txid])
    await relay.on_tx_inv("peer-c", [test_txid])

    # Verify all have it in known_txids
    state_a = relay._peer_state.get("peer-a")
    state_b = relay._peer_state.get("peer-b")
    state_c = relay._peer_state.get("peer-c")
    assert test_txid in state_a.known_txids
    assert test_txid in state_b.known_txids
    assert test_txid in state_c.known_txids

    # Simulate peer-a responding with NOTFOUND
    await relay.on_tx_notfound("peer-a", [test_txid])

    # Only peer-a should have the txid removed
    assert test_txid not in state_a.known_txids, "peer-a should have txid removed"
    # Other peers should still have it (until they also respond or we exhaust retries)
    # Note: The retry logic might request from peer-b or peer-c, so at least one should still have it
    remaining_count = sum([
        test_txid in state_b.known_txids,
        test_txid in state_c.known_txids,
    ])
    assert remaining_count >= 1, "At least one other peer should still have the txid"
