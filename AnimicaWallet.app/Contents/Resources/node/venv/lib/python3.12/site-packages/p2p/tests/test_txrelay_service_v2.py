import asyncio
import hashlib

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_txid_must_match_bytes_hash() -> None:
    admitted: list[bytes] = []

    async def send_noop(_peer: str, _payload):
        return None

    async def has_tx(_txid: bytes) -> bool:
        return False

    async def has_chain_tx(_txid: bytes) -> bool:
        return False

    async def get_tx_raw(_txid: bytes):
        return None

    async def admit_tx(raw: bytes, _origin: str | None):
        admitted.append(raw)
        return True, None

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

    bad_tx = b"bad-tx"
    bad_hash = hashlib.sha3_256(b"other-tx").digest()
    await relay.on_tx_data("peer-a", [{"txid": bad_hash, "tx_bytes": bad_tx}])
    assert admitted == []
    state = relay.tx_state_for(bad_hash)
    assert state is not None
    assert state["state"] == "received_invalid"


@pytest.mark.asyncio
async def test_inflight_timeout_retries() -> None:
    sent_get: list[tuple[str, list[bytes]]] = []

    async def send_noop(_peer: str, _payload):
        return None

    async def send_get(peer: str, txids: list[bytes]):
        sent_get.append((peer, list(txids)))

    async def has_tx(_txid: bytes) -> bool:
        return False

    async def has_chain_tx(_txid: bytes) -> bool:
        return False

    async def get_tx_raw(_txid: bytes):
        return None

    async def admit_tx(_raw: bytes, _origin: str | None):
        return False, "reject"

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        inflight_timeout_s=0.1,
        inflight_max_retries=2,
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

    txid = hashlib.sha3_256(b"tx-timeout").digest()
    await relay.on_tx_inv("peer-a", [txid])
    await relay.on_tx_inv("peer-b", [txid])

    task = asyncio.create_task(relay.inflight_timeout_loop())
    try:
        await asyncio.sleep(1.1)
    finally:
        relay._running = False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    peers = [peer for peer, _txids in sent_get]
    assert "peer-a" in peers
    assert "peer-b" in peers


@pytest.mark.asyncio
async def test_tx_state_requested_on_inv() -> None:
    sent_get: list[tuple[str, list[bytes]]] = []

    async def send_noop(_peer: str, _payload):
        return None

    async def send_get(peer: str, txids: list[bytes]):
        sent_get.append((peer, list(txids)))

    async def has_tx(_txid: bytes) -> bool:
        return False

    async def has_chain_tx(_txid: bytes) -> bool:
        return False

    async def get_tx_raw(_txid: bytes):
        return None

    async def admit_tx(_raw: bytes, _origin: str | None):
        return True, None

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        inflight_timeout_s=1.0,
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

    txid = hashlib.sha3_256(b"inv-requested").digest()
    await relay.on_tx_inv("peer-a", [txid])
    assert sent_get
    state = relay.tx_state_for(txid)
    assert state is not None
    assert state["state"] == "requested"


@pytest.mark.asyncio
async def test_invalid_tx_enters_terminal_state_and_cooldown() -> None:
    sent_get: list[tuple[str, list[bytes]]] = []

    async def send_noop(_peer: str, _payload):
        return None

    async def send_get(peer: str, txids: list[bytes]):
        sent_get.append((peer, list(txids)))

    async def has_tx(_txid: bytes) -> bool:
        return False

    async def has_chain_tx(_txid: bytes) -> bool:
        return False

    async def get_tx_raw(_txid: bytes):
        return None

    async def admit_tx(_raw: bytes, _origin: str | None):
        return False, "pq_verify"

    async def list_hashes(_limit: int):
        return []

    relay = TxRelayService(
        max_tx_bytes=1024,
        invalid_tx_cooldown_s=600.0,
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

    tx_raw = b"bad-signature-tx"
    txid = hashlib.sha3_256(tx_raw).digest()
    await relay.on_tx_data("peer-a", [{"txid": txid, "tx_bytes": tx_raw}])

    state = relay.tx_state_for(txid)
    assert state is not None
    assert state["state"] == "invalid_final"

    requested = await relay.request_missing_known(limit=1, trigger="test")
    assert requested == 0
