from rpc import mempool_service


def test_to_mempool_reject_internal_error_shape():
    rej = mempool_service._to_mempool_reject(reason="internal_error", message="mempool admission failed", context={"tx_hash": "0xabc"}, exc=TypeError("x"))
    payload = rej.to_dict()
    assert payload["reason"] == "internal_error"
    assert payload["code"] == 2999
    assert payload["context"]["tx_hash"] == "0xabc"
    assert payload["context"]["error_class"] == "TypeError"
