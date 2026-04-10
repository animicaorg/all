from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from p2p.txrelay import TxRelayService


@pytest.mark.asyncio
async def test_mempool_summary_triggers_missing_fetch():
    send_tx_get = AsyncMock()

    service = TxRelayService(
        max_tx_bytes=1_000_000,
        peer_ids=MagicMock(return_value=["peerA"]),
        peer_eligible=MagicMock(return_value=True),
        send_tx_inv=AsyncMock(),
        send_tx_get=send_tx_get,
        send_tx_data=AsyncMock(),
        send_tx_notfound=AsyncMock(),
        send_mempool_req=AsyncMock(),
        send_mempool_resp=AsyncMock(),
        has_tx=AsyncMock(return_value=False),
        has_chain_tx=AsyncMock(return_value=False),
        get_tx_raw=AsyncMock(return_value=None),
        admit_tx=AsyncMock(return_value=(True, None)),
        list_mempool_hashes=AsyncMock(return_value=[]),
    )

    txid = b"\x11" * 32
    await service.on_mempool_summary("peerA", [txid], count=1)

    assert service.metrics()["mempool_summary_recv"] == 1
    assert send_tx_get.await_count == 1
    assert send_tx_get.await_args.args[0] == "peerA"
    assert txid in send_tx_get.await_args.args[1]
