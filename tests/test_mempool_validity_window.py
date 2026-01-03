from __future__ import annotations

from unittest.mock import Mock

import pytest

from core.encoding.cbor import dumps as cbor_dumps
from core.utils.hash import sha3_256
from mempool.errors import AdmissionError
from mempool.pool import Pool, PoolConfig
from rpc.mempool_service import MempoolService


def test_mempool_rejects_missing_validity_window_context() -> None:
    pool = Pool(cfg=PoolConfig(max_txs=100, max_bytes=1024 * 1024))
    state_db = Mock()
    service = MempoolService(
        pool=pool,
        chain_id=1337,
        min_gas_price_wei=0,
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
    )

    sender_bytes = bytes.fromhex("ab" * 32)
    body = {
        "from": sender_bytes,
        "gasLimit": 21000,
        "maxFee": 1,
        "chainId": 1337,
    }
    tx_envelope = {"body": body}
    raw = cbor_dumps(tx_envelope)
    tx_envelope["raw"] = raw
    tx_hash = "0x" + sha3_256(raw).hex()

    with pytest.raises(AdmissionError) as exc_info:
        service.submit(tx=tx_envelope, raw=raw, tx_hash_hex=tx_hash, local=True)

    context = exc_info.value.context
    assert context["tx_hash"] == tx_hash
    assert context["sender"] == "0x" + sender_bytes.hex()
    assert context["missing"] == ["validAfter", "validUntil", "salt"]
    assert context["expected_location"] == "tx.body"
