from __future__ import annotations

from core.utils.tx import normalize_tx_bytes, serialize_tx_canonical, txid
from mempool.tx_hash import tx_hash_bytes


def _sample_tx() -> dict:
    return {
        "tx": {
            "chainId": 1337,
            "from": b"\x01" * 32,
            "nonce": 7,
            "gas": {"price": 2, "limit": 21000},
            "payload": {"t": 0, "v": {"to": b"\x02" * 32, "amount": 123, "data": b""}},
            "accessList": [],
            "v": 1,
        },
        "sigs": [],
    }


def test_canonical_serialization_and_txid_stable_round_trip():
    tx = _sample_tx()
    raw1 = serialize_tx_canonical(tx)
    raw2 = normalize_tx_bytes(raw1)
    assert raw1 == raw2
    assert txid(raw1) == txid(raw2)
    assert txid(raw1) == tx_hash_bytes(raw1)
