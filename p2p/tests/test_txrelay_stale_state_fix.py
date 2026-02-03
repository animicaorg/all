"""
Test for fix: Clear stale accepted_in_mempool state when peer announces transaction.

This test verifies that when a transaction is marked as "accepted_in_mempool"
but then announced again by a peer (e.g., after eviction or state inconsistency),
the system will clear the stale state and re-request the transaction.
"""
import asyncio
import hashlib

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_stale_accepted_state_cleared_on_new_announcement() -> None:
    """
    Reproduces the bug where transactions are in known_txids but not in mempool.
    
    Scenario:
    1. Transaction is announced by peer A and admitted to mempool
    2. Transaction is evicted/removed from mempool (simulated by has_tx returning False)
    3. Peer B announces the same transaction
    4. System should clear the stale "accepted_in_mempool" state and request the tx
    """
    sent_get: list[tuple[str, list[bytes]]] = []
    admitted_txs: set[bytes] = set()
    has_tx_results: dict[bytes, bool] = {}  # Control what has_tx returns

    async def send_noop(_peer: str, _payload):
        return None

    async def send_get(peer: str, txids: list[bytes]):
        sent_get.append((peer, list(txids)))

    async def has_tx(txid: bytes) -> bool:
        # Return what the test configured
        return has_tx_results.get(txid, False)

    async def has_chain_tx(_txid: bytes) -> bool:
        return False

    async def get_tx_raw(_txid: bytes):
        return None

    async def admit_tx(raw: bytes, _origin: str | None):
        txid = hashlib.sha3_256(raw).digest()
        admitted_txs.add(txid)
        return True, None

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        inflight_timeout_s=1.0,
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

    # Step 1: Peer A announces transaction
    tx_bytes = b"test-transaction-data"
    txid = hashlib.sha3_256(tx_bytes).digest()
    
    # Transaction is NOT in mempool yet
    has_tx_results[txid] = False
    
    await relay.on_tx_inv("peer-a", [txid])
    
    # Should have requested the transaction
    assert len(sent_get) == 1
    assert sent_get[0][0] == "peer-a"
    assert txid in sent_get[0][1]
    
    # Clear the sent_get list for next check
    sent_get.clear()
    
    # Step 2: Receive transaction data and admit it
    await relay.on_tx_data("peer-a", [{"txid": txid, "tx_bytes": tx_bytes}])
    
    # Transaction should be admitted
    assert txid in admitted_txs
    
    # State should be "accepted_in_mempool"
    state = relay.tx_state_for(txid)
    assert state is not None
    assert state["state"] == "accepted_in_mempool"
    
    # Step 3: Simulate transaction eviction - has_tx now returns False
    # but state is still "accepted_in_mempool"
    has_tx_results[txid] = False
    admitted_txs.remove(txid)  # Simulate eviction
    
    # Step 4: Peer B announces the same transaction
    await relay.on_tx_inv("peer-b", [txid])
    
    # BEFORE FIX: sent_get would be empty because state is "accepted_in_mempool"
    # AFTER FIX: System should clear stale state and request the transaction
    assert len(sent_get) == 1, "Transaction should be requested after state is cleared"
    assert sent_get[0][0] == "peer-b"
    assert txid in sent_get[0][1]
    
    # Verify state was cleared and re-requested
    state_after = relay.tx_state_for(txid)
    assert state_after is not None
    # State should be "requested" now, not "accepted_in_mempool"
    assert state_after["state"] == "requested"


@pytest.mark.asyncio
async def test_valid_accepted_state_not_cleared() -> None:
    """
    Verify that valid "accepted_in_mempool" state is preserved.
    
    When has_tx returns True, the transaction should not be requested again.
    """
    sent_get: list[tuple[str, list[bytes]]] = []
    admitted_txs: set[bytes] = set()

    async def send_noop(_peer: str, _payload):
        return None

    async def send_get(peer: str, txids: list[bytes]):
        sent_get.append((peer, list(txids)))

    async def has_tx(txid: bytes) -> bool:
        return txid in admitted_txs

    async def has_chain_tx(_txid: bytes) -> bool:
        return False

    async def get_tx_raw(_txid: bytes):
        return None

    async def admit_tx(raw: bytes, _origin: str | None):
        txid = hashlib.sha3_256(raw).digest()
        admitted_txs.add(txid)
        return True, None

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        inflight_timeout_s=1.0,
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

    # Step 1: Peer A announces and we receive the transaction
    tx_bytes = b"test-transaction-in-mempool"
    txid = hashlib.sha3_256(tx_bytes).digest()
    
    await relay.on_tx_inv("peer-a", [txid])
    assert len(sent_get) == 1
    sent_get.clear()
    
    await relay.on_tx_data("peer-a", [{"txid": txid, "tx_bytes": tx_bytes}])
    assert txid in admitted_txs
    
    # Step 2: Peer B announces the same transaction
    # Since has_tx returns True (tx is in mempool), it should NOT be requested
    await relay.on_tx_inv("peer-b", [txid])
    
    # Should NOT request since transaction is validly in mempool
    assert len(sent_get) == 0, "Should not request tx that is actually in mempool"
    
    # State should still be "accepted_in_mempool"
    state = relay.tx_state_for(txid)
    assert state is not None
    assert state["state"] == "accepted_in_mempool"
