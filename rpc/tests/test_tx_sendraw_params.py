from __future__ import annotations

import pytest

from rpc.tests import new_test_client, rpc_call
from rpc.methods.tx import normalize_send_raw_tx_params


def test_normalize_send_raw_tx_params_supported_shapes() -> None:
    raw_hex = "0x0102"

    cases = [
        [raw_hex],
        ["0102"],
        {"rawTx": raw_hex},
        {"raw_tx": raw_hex},
        {"tx": raw_hex},
        {"raw": raw_hex},
        {"cbor": raw_hex},
        {"txBytes": raw_hex},
        [{"rawTx": raw_hex}],
        [{"raw_tx": raw_hex}],
    ]

    for params in cases:
        raw, meta = normalize_send_raw_tx_params(params)
        assert raw == b"\x01\x02"
        assert meta["size_bytes"] == 2


def test_normalize_send_raw_tx_params_rejects_invalid_hex() -> None:
    with pytest.raises(Exception):
        normalize_send_raw_tx_params({"rawTx": "0xabc"})
    with pytest.raises(Exception):
        normalize_send_raw_tx_params({"rawTx": "0xzz"})
    with pytest.raises(Exception):
        normalize_send_raw_tx_params({})


def test_send_raw_tx_accepts_list_not_invalid_params() -> None:
    client, _, _ = new_test_client()
    res = rpc_call(client, "tx.sendRawTransaction", ["0x00"], expect_error=True)
    assert res["error"]["code"] != -32602


def test_send_raw_tx_accepts_named_rawtx_not_invalid_params() -> None:
    client, _, _ = new_test_client()
    res = rpc_call(client, "tx.sendRawTransaction", {"rawTx": "0x00"}, expect_error=True)
    assert res["error"]["code"] != -32602


def test_send_raw_tx_accepts_named_raw_tx_not_invalid_params() -> None:
    client, _, _ = new_test_client()
    res = rpc_call(client, "tx.sendRawTransaction", {"raw_tx": "0x00"}, expect_error=True)
    assert res["error"]["code"] != -32602
