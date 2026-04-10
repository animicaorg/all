from __future__ import annotations

import base64

import pytest

from rpc import errors as rpc_errors
from rpc.methods.tx import normalize_send_raw_tx_params


def test_normalize_send_raw_tx_params_supported_shapes() -> None:
    raw_hex = "0x0102"
    raw_b64 = base64.b64encode(b"\x01\x02").decode("ascii")

    cases = [
        [raw_hex],
        {"rawTx": raw_hex},
        {"raw_tx": raw_hex},
        {"rawtx": raw_hex},
        {"tx": raw_hex},
        {"raw": raw_hex},
        {"cbor": raw_hex},
        {"txBytes": raw_hex},
        [{"rawTx": raw_hex}],
        [{"raw_tx": raw_hex}],
        raw_hex,
        raw_b64,
        {"tx": raw_b64},
        {"params": [raw_hex]},
        [list(b"\x01\x02")],
    ]

    for params in cases:
        raw, meta = normalize_send_raw_tx_params(params)
        assert raw == b"\x01\x02"
        assert meta["size_bytes"] == 2


def test_normalize_send_raw_tx_params_rejects_invalid_shapes_with_rich_data() -> None:
    with pytest.raises(rpc_errors.InvalidParams) as exc:
        normalize_send_raw_tx_params({"foo": "!!!"})
    err = exc.value
    assert err.code == -32602
    assert err.data and err.data.get("expected")
    assert err.data and err.data.get("normalize_attempts")
    assert "Field" in err.message
