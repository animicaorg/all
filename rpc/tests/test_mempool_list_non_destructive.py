import pytest

from rpc.tests import new_test_client, rpc_call
from rpc.tests.test_tx_send_mempool_visibility import _build_signed_transfer_cbor

pytestmark = pytest.mark.anyio


def test_mempool_list_is_non_destructive() -> None:
    client, cfg, _ = new_test_client()
    cbor_tx, exp_tx_hash, _sender = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=0)
    raw_hex = "0x" + cbor_tx.hex()

    submit_res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert submit_res["result"] == exp_tx_hash

    first = rpc_call(client, "mempool.getPending", params={})["result"]
    second = rpc_call(client, "mempool.getPending", params={})["result"]

    assert exp_tx_hash in first
    assert exp_tx_hash in second
