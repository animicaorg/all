import asyncio
import hashlib
import time

import pytest

from p2p.txrelay import TxRelayService


class MockMempool:
    def __init__(self, name: str) -> None:
        self.name = name
        self._txs: dict[bytes, bytes] = {}

    async def admit_tx(self, raw: bytes, origin: str | None = None) -> tuple[bool, str | None]:
        tx_hash = hashlib.sha3_256(raw).digest()
        if tx_hash in self._txs:
            return True, "duplicate"
        self._txs[tx_hash] = raw
        return True, None

    async def has_tx(self, tx_hash: bytes) -> bool:
        return tx_hash in self._txs

    async def get_tx_raw(self, tx_hash: bytes) -> bytes | None:
        return self._txs.get(tx_hash)

    async def list_pending_hashes(self, limit: int = 512) -> list[bytes]:
        return list(self._txs.keys())[:limit]

    def remove_included(self, hashes: list[bytes]) -> None:
        for h in hashes:
            self._txs.pop(h, None)

    def count(self) -> int:
        return len(self._txs)


async def _wait_for(predicate, timeout_s: float = 5.0) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met within timeout")


@pytest.mark.asyncio
async def test_tx_propagates_between_two_nodes() -> None:
    mempool_a = MockMempool("A")
    mempool_b = MockMempool("B")

    async def send_noop(_peer: str, _payload: object) -> None:
        return None

    async def send_tx_inv_a(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-a", txids)

    async def send_tx_inv_b(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_inv("peer-b", txids)

    async def send_tx_get_a(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-b":
            await relay_b.on_tx_get("peer-a", txids)

    async def send_tx_get_b(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_get("peer-b", txids)

    async def send_tx_data_a(peer: str, items: list[dict]) -> None:
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-a", items)

    async def send_tx_data_b(peer: str, items: list[dict]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_data("peer-b", items)

    relay_a = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-b"],
        peer_eligible=lambda _: True,
        send_tx_inv=send_tx_inv_a,
        send_tx_get=send_tx_get_a,
        send_tx_data=send_tx_data_a,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_a.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_a.get_tx_raw,
        admit_tx=mempool_a.admit_tx,
        list_mempool_hashes=mempool_a.list_pending_hashes,
    )
    relay_b = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-a"],
        peer_eligible=lambda _: True,
        send_tx_inv=send_tx_inv_b,
        send_tx_get=send_tx_get_b,
        send_tx_data=send_tx_data_b,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_b.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_b.get_tx_raw,
        admit_tx=mempool_b.admit_tx,
        list_mempool_hashes=mempool_b.list_pending_hashes,
    )
    relay_a.register_peer("peer-b", peer_node_id="node-b")
    relay_b.register_peer("peer-a", peer_node_id="node-a")

    inv_task_a = asyncio.create_task(relay_a.inv_flush_loop())
    inv_task_b = asyncio.create_task(relay_b.inv_flush_loop())

    raw_tx = b"tx-prop-" + str(time.time()).encode()
    txid = hashlib.sha3_256(raw_tx).digest()
    await mempool_a.admit_tx(raw_tx, origin="local")
    await relay_a.on_mempool_add(txid, raw_tx)

    await _wait_for(lambda: mempool_b.has_tx(txid), timeout_s=5.0)

    inv_task_a.cancel()
    inv_task_b.cancel()


@pytest.mark.asyncio
async def test_remote_miner_includes_propagated_tx() -> None:
    mempool_a = MockMempool("A")
    mempool_b = MockMempool("B")

    async def send_noop(_peer: str, _payload: object) -> None:
        return None

    async def send_tx_inv_a(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-b":
            await relay_b.on_tx_inv("peer-a", txids)

    async def send_tx_inv_b(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_inv("peer-b", txids)

    async def send_tx_get_a(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-b":
            await relay_b.on_tx_get("peer-a", txids)

    async def send_tx_get_b(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_get("peer-b", txids)

    async def send_tx_data_a(peer: str, items: list[dict]) -> None:
        if peer == "peer-b":
            await relay_b.on_tx_data("peer-a", items)

    async def send_tx_data_b(peer: str, items: list[dict]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_data("peer-b", items)

    relay_a = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-b"],
        peer_eligible=lambda _: True,
        send_tx_inv=send_tx_inv_a,
        send_tx_get=send_tx_get_a,
        send_tx_data=send_tx_data_a,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_a.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_a.get_tx_raw,
        admit_tx=mempool_a.admit_tx,
        list_mempool_hashes=mempool_a.list_pending_hashes,
    )
    relay_b = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-a"],
        peer_eligible=lambda _: True,
        send_tx_inv=send_tx_inv_b,
        send_tx_get=send_tx_get_b,
        send_tx_data=send_tx_data_b,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_b.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_b.get_tx_raw,
        admit_tx=mempool_b.admit_tx,
        list_mempool_hashes=mempool_b.list_pending_hashes,
    )
    relay_a.register_peer("peer-b", peer_node_id="node-b")
    relay_b.register_peer("peer-a", peer_node_id="node-a")

    inv_task_a = asyncio.create_task(relay_a.inv_flush_loop())
    inv_task_b = asyncio.create_task(relay_b.inv_flush_loop())

    raw_tx = b"tx-mine-" + str(time.time()).encode()
    txid = hashlib.sha3_256(raw_tx).digest()
    await mempool_a.admit_tx(raw_tx, origin="local")
    await relay_a.on_mempool_add(txid, raw_tx)

    await _wait_for(lambda: mempool_b.has_tx(txid), timeout_s=5.0)

    mined_txs = await mempool_b.list_pending_hashes(limit=100)
    assert txid in mined_txs

    mempool_b.remove_included([txid])
    mempool_a.remove_included([txid])

    assert mempool_b.count() == 0
    assert mempool_a.count() == 0

    inv_task_a.cancel()
    inv_task_b.cancel()


@pytest.mark.asyncio
async def test_tx_fetch_retries_other_peer() -> None:
    mempool_b = MockMempool("B")
    mempool_c = MockMempool("C")
    mempool_a = MockMempool("A")

    async def send_noop(_peer: str, _payload: object) -> None:
        return None

    async def send_tx_inv_b(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_inv("peer-b", txids)

    async def send_tx_inv_c(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_inv("peer-c", txids)

    async def send_tx_get_a(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-b":
            return None
        if peer == "peer-c":
            await relay_c.on_tx_get("peer-a", txids)

    async def send_tx_get_c(peer: str, txids: list[bytes]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_get("peer-c", txids)

    async def send_tx_data_c(peer: str, items: list[dict]) -> None:
        if peer == "peer-a":
            await relay_a.on_tx_data("peer-c", items)

    relay_a = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        inflight_timeout_s=0.2,
        inflight_max_retries=2,
        request_cooldown_s=0.1,
        peer_ids=lambda: ["peer-b", "peer-c"],
        peer_eligible=lambda _: True,
        send_tx_inv=send_noop,
        send_tx_get=send_tx_get_a,
        send_tx_data=send_noop,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_a.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_a.get_tx_raw,
        admit_tx=mempool_a.admit_tx,
        list_mempool_hashes=mempool_a.list_pending_hashes,
    )
    relay_b = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-a"],
        peer_eligible=lambda _: True,
        send_tx_inv=send_tx_inv_b,
        send_tx_get=send_noop,
        send_tx_data=send_noop,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_b.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_b.get_tx_raw,
        admit_tx=mempool_b.admit_tx,
        list_mempool_hashes=mempool_b.list_pending_hashes,
    )
    relay_c = TxRelayService(
        max_tx_bytes=1024 * 1024,
        inv_batch_size=200,
        inv_flush_interval_s=0.05,
        peer_ids=lambda: ["peer-a"],
        peer_eligible=lambda _: True,
        send_tx_inv=send_tx_inv_c,
        send_tx_get=send_tx_get_c,
        send_tx_data=send_tx_data_c,
        send_tx_notfound=send_noop,
        send_mempool_req=send_noop,
        send_mempool_resp=send_noop,
        has_tx=mempool_c.has_tx,
        has_chain_tx=lambda _: asyncio.sleep(0, False),
        get_tx_raw=mempool_c.get_tx_raw,
        admit_tx=mempool_c.admit_tx,
        list_mempool_hashes=mempool_c.list_pending_hashes,
    )
    relay_a.register_peer("peer-b", peer_node_id="node-b")
    relay_a.register_peer("peer-c", peer_node_id="node-c")
    relay_b.register_peer("peer-a", peer_node_id="node-a")
    relay_c.register_peer("peer-a", peer_node_id="node-a")

    inflight_task = asyncio.create_task(relay_a.inflight_timeout_loop())
    inv_task_b = asyncio.create_task(relay_b.inv_flush_loop())
    inv_task_c = asyncio.create_task(relay_c.inv_flush_loop())

    raw_tx = b"tx-retry-" + str(time.time()).encode()
    txid = hashlib.sha3_256(raw_tx).digest()

    await mempool_b.admit_tx(raw_tx, origin="peer-b")
    await mempool_c.admit_tx(raw_tx, origin="peer-c")

    await relay_b.on_mempool_add(txid, raw_tx)
    await relay_c.on_mempool_add(txid, raw_tx)

    await _wait_for(lambda: mempool_a.has_tx(txid), timeout_s=5.0)

    inflight_task.cancel()
    inv_task_b.cancel()
    inv_task_c.cancel()
