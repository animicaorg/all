from rpc.mempool_service import _tx_gas_limit


def test_tx_gas_limit_returns_zero_for_dict_value() -> None:
    tx = {"body": {"gasLimit": {"bad": "type"}}}
    assert _tx_gas_limit(tx) == 0
