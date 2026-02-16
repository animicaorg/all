from __future__ import annotations

import pytest

from rpc.methods.tx import normalize_send_raw_tx_params


def test_normalize_send_raw_tx_params_supported_shapes() -> None:
    raw_hex = "0x0102"

    cases = [
        [raw_hex],
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


def test_normalize_send_raw_tx_params_rejects_invalid_shapes() -> None:
    with pytest.raises(Exception):
        normalize_send_raw_tx_params({"rawTx": "0xabc"})
    with pytest.raises(Exception):
        normalize_send_raw_tx_params({"rawTx": "0xzz"})
    with pytest.raises(Exception):
        normalize_send_raw_tx_params({})
    with pytest.raises(Exception):
        normalize_send_raw_tx_params([{"foo": "0x00"}])
    with pytest.raises(Exception):
        normalize_send_raw_tx_params(["0102"])


def test_normalize_send_raw_tx_params_invalid_shape_actionable_error() -> None:
    with pytest.raises(Exception) as exc:
        normalize_send_raw_tx_params({"foo": "0x00"})
    assert 'expected params ["0x.."] or {rawTx:"0x.."}' in str(exc.value)
