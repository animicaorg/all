from mempool.rejects import RejectReason, REJECT_CODE, reject


def test_reject_codes_are_stable_and_non_1000():
    for reason in RejectReason:
        assert REJECT_CODE[reason] != 1000


def test_internal_error_includes_error_class():
    r = reject(RejectReason.internal_error, message="boom", hint="check logs", context={"tx_hash": "0x01"}, error_class="TypeError")
    d = r.to_dict()
    assert d["reason"] == "internal_error"
    assert d["error_class"] == "TypeError"
    assert d["context"]["error_class"] == "TypeError"
