from __future__ import annotations

import pytest

from core.utils.tx import TxNormalizationError, normalize_tx_body


def test_normalize_tx_body_rejects_ambiguous_dict_numeric_field() -> None:
    body = {
        "from": "0x" + "00" * 32,
        "to": "0x" + "11" * 32,
        "nonce": {"unexpected": 1},
        "value": 10,
        "gasLimit": 21000,
        "maxFee": 1,
        "chainId": 1,
    }

    with pytest.raises(TxNormalizationError) as exc_info:
        normalize_tx_body(body)

    exc = exc_info.value
    assert exc.reason == "bad_field_type"
    assert exc.details.get("field") == "nonce"
    assert exc.details.get("received_type") == "dict"
    assert "unexpected" in exc.details.get("received_keys", [])


def test_normalize_tx_body_accepts_gas_object_with_price_and_limit() -> None:
    body = {
        "from": "0x" + "00" * 32,
        "to": "0x" + "11" * 32,
        "nonce": 0,
        "value": 10,
        "gas": {"price": 1, "limit": 21000},
        "chainId": 1,
    }

    normalized = normalize_tx_body(body)

    assert normalized["gas"]["price"] == 1
    assert normalized["gas"]["limit"] == 21000


def test_normalize_tx_body_rejects_gas_limit_dict() -> None:
    body = {
        "from": "0x" + "00" * 32,
        "to": "0x" + "11" * 32,
        "nonce": 0,
        "value": 10,
        "gasLimit": {"price": 1, "limit": 21000},
        "maxFee": 1,
        "chainId": 1,
    }

    with pytest.raises(TxNormalizationError) as exc_info:
        normalize_tx_body(body)

    exc = exc_info.value
    assert exc.reason == "bad_field_type"
    assert exc.details.get("field") == "gasLimit"
    assert exc.details.get("received_type") == "dict"
    assert set(exc.details.get("received_keys", [])) == {"limit", "price"}
