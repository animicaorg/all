import pytest

from rpc.tests import new_test_client, rpc_call
from rpc.tests.test_tx_send_mempool_visibility import _build_signed_transfer_cbor

pytestmark = pytest.mark.anyio


async def test_tx_get_status_pending_then_confirmed():
    client, cfg, _tmp = new_test_client()
    cbor_tx, tx_hash, sender = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=0)
    raw_hex = "0x" + cbor_tx.hex()

    submit_res = rpc_call(client, "tx.sendRawTransaction", params={"rawTx": raw_hex})
    assert submit_res["result"] == tx_hash

    status_pending = rpc_call(client, "tx.getStatus", params=[tx_hash])["result"]
    assert status_pending["seen_in_mempool"] is True
    assert status_pending["included_in_block_hash"] is None
    assert status_pending["status"] in {"pending", "confirmed"}

    rpc_call(client, "miner.mine", {"count": 1, "address": sender})

    status_confirmed = rpc_call(client, "tx.getStatus", params=[tx_hash])["result"]
    assert status_confirmed["status"] == "confirmed"
    assert status_confirmed["included_in_block_hash"] is not None
    assert status_confirmed["confirmations"] is None or status_confirmed["confirmations"] >= 1
