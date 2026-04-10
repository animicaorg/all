from __future__ import annotations

import pytest

from coretx.errors import RejectReason, TxReject
from rpc import errors as rpc_errors
from rpc.methods import tx2


class _DummyMempool:
    def admit_tx(self, envelope, source, peer_id):
        return (
            False,
            TxReject(
                reason=RejectReason.scheme_unsupported,
                code=2003,
                message="Signature scheme unsupported",
                hint="Update wallet",
                context={"kind": "scheme_unsupported"},
            ),
        )


@pytest.mark.asyncio
async def test_signature_verify_errors_map_to_invalid_tx(monkeypatch):
    monkeypatch.setattr(tx2, "get_mempool2_service", lambda: _DummyMempool())
    monkeypatch.setattr(tx2, "decode_tx_envelope", lambda _b: type("E", (), {"txid": type("T", (), {"bytes32": b"\\x00" * 32, "hex": lambda self: "00" * 32})(), "body": type("B", (), {"chain_id": 1, "nonce": 0})()})())

    with pytest.raises(rpc_errors.RpcError) as ei:
        await tx2.send_raw_transaction_v2("00")

    assert ei.value.code == int(rpc_errors.AnimicaCode.INVALID_TX)
    assert ei.value.code != -32603
    assert ei.value.code != -32012
