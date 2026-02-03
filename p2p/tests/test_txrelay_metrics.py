import hashlib

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_txrelay_metrics_counts() -> None:
    async def send_noop(_peer: str, _payload):
        return None

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

    raw = b"tx-relay-metrics"
    txid = hashlib.sha3_256(raw).digest()

    await relay.on_tx_inv("peer-a", [txid])
    await relay.on_tx_data("peer-a", [{"txid": txid, "tx_bytes": raw}])

    metrics = relay.metrics()
    assert metrics["inv_recv"] == 1
    assert metrics["get_sent"] == 1
    assert metrics["requested_count"] == 1
    assert metrics["data_recv"] == 1
    assert metrics["received_count"] == 1
    assert metrics["accepted_count"] == 1
