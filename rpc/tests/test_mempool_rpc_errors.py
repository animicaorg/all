from __future__ import annotations

import pytest

from mempool import errors as mp_errors
from rpc import errors as rpc_errors


@pytest.mark.parametrize(
    "exc, expected_code",
    [
        (
            mp_errors.FeeTooLow(offered_gas_price_wei=1, min_required_wei=10),
            rpc_errors.AnimicaCode.FEE_TOO_LOW,
        ),
        (
            mp_errors.NonceGap(expected_nonce=2, got_nonce=5),
            rpc_errors.AnimicaCode.NONCE_TOO_LOW,
        ),
        (
            mp_errors.Oversize(size_bytes=2048, max_bytes=1024),
            rpc_errors.AnimicaCode.TX_TOO_LARGE,
        ),
    ],
)
def test_mempool_errors_are_preserved_in_rpc(
    exc: mp_errors.MempoolError, expected_code: rpc_errors.AnimicaCode
):
    """
    MempoolError instances should be mapped into RpcError with stable Animica
    codes and include the original mempool payload under data.mempoolError.
    """

    rpc_err = rpc_errors.to_error(exc)

    assert isinstance(rpc_err, rpc_errors.RpcError)
    assert rpc_err.code == expected_code
    assert rpc_err.data and "mempoolError" in rpc_err.data

    payload = rpc_err.data["mempoolError"]
    assert payload["code"] == exc.code
    assert payload["reason"] == exc.reason
