import hashlib

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_request_missing_known_returns_details_with_bounds():
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
        return False, "not_used"

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        peer_ids=lambda: ["peer-a", "peer-b", "peer-c"],
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

    for peer in ("peer-a", "peer-b", "peer-c"):
        relay.register_peer(peer, peer_node_id=peer)

    txids = [hashlib.sha3_256(f"tx-{i}".encode()).digest() for i in range(10)]
    for txid in txids:
        relay._peer_state["peer-a"].known_txids.add(txid)
        relay._peer_state["peer-b"].known_txids.add(txid)

    details = await relay.request_missing_known(
        limit=10,
        force=True,
        max_peers=1,
        batch_size=3,
        include_details=True,
    )

    assert details["requested"] == 10
    assert details["max_peers"] == 1
    assert details["batch_size"] == 3
    assert details["requested_peers"] == ["peer-a"]
    assert len(sent_get) == 4  # 10 txids with batch size 3 -> 4 batches


@pytest.mark.asyncio
async def test_confirmed_txid_is_not_reimported_from_peer_known_inventory():
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
        return False, "not_used"

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        peer_ids=lambda: ["peer-a"],
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

    relay.register_peer("peer-a", peer_node_id="peer-a")
    txid = hashlib.sha3_256(b"confirmed-never-reimport").digest()
    relay._peer_state["peer-a"].known_txids.add(txid)

    reconcile_result = relay.on_block_accepted([txid])
    assert reconcile_result["confirmed"] == 1
    state = relay.tx_state_for(txid)
    assert state is not None
    assert state["state"] == "confirmed"
    assert state["mempool_status"] == "confirmed"

    details = await relay.request_missing_known(
        limit=1,
        trigger="test_confirmed",
        include_details=True,
    )
    assert details["requested"] == 0
    assert details["skip_reasons"][f"0x{txid.hex()}"] == "terminal_ok"
    assert sent_get == []

    # Even after a fresh peer INV announcement, confirmed txs must remain terminal.
    await relay.on_tx_inv("peer-a", [txid])
    assert sent_get == []
    state_after_inv = relay.tx_state_for(txid)
    assert state_after_inv is not None
    assert state_after_inv["state"] == "confirmed"
