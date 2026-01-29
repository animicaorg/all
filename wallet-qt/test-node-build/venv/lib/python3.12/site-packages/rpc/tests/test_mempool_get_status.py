import pytest

from rpc.tests import new_test_client, rpc_call
from rpc.tests.test_tx_send_mempool_visibility import _build_signed_transfer_cbor

pytestmark = pytest.mark.anyio


def test_mempool_get_status_reports_pending() -> None:
    client, cfg, _ = new_test_client()
    cbor_tx, exp_tx_hash, _sender = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=0)
    raw_hex = "0x" + cbor_tx.hex()

    submit_res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert submit_res["result"] == exp_tx_hash

    status = rpc_call(client, "mempool.getStatus", params=[exp_tx_hash])["result"]
    assert status["hash"] == exp_tx_hash
    assert status["known"] is True
    assert status["state"] == "pending"


def test_mempool_get_status_reports_unknown() -> None:
    client, _cfg, _ = new_test_client()
    unknown_hash = "0x" + "00" * 32

    status = rpc_call(client, "mempool.getStatus", params=[unknown_hash])["result"]
    assert status["hash"] == unknown_hash
    assert status["known"] is False
    assert status["state"] == "unknown"


def test_mempool_get_status_reports_rejected_nonce_too_low() -> None:
    client, cfg, _ = new_test_client()
    cbor_tx, exp_tx_hash, sender = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=0)
    raw_hex = "0x" + cbor_tx.hex()

    from core.utils.address import address_to_bytes
    from rpc import deps

    ctx = deps.get_ctx()
    addr_bytes = address_to_bytes(sender)
    ctx.state_db.set_nonce(addr_bytes, 1)

    submit = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex}, expect_error=True)
    assert submit["error"]["code"] == -32014

    status = rpc_call(client, "mempool.getStatus", params=[exp_tx_hash])["result"]
    assert status["hash"] == exp_tx_hash
    assert status["known"] is True
    assert status["state"] == "rejected"
    assert status["reason"] == "nonce_too_low"
