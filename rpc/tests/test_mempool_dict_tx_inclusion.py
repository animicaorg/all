import pytest

from rpc import deps
from rpc.tests import new_test_client, rpc_call
from rpc.tests.test_tx_send_mempool_visibility import _build_signed_transfer_cbor

pytestmark = pytest.mark.anyio


def test_dict_envelope_in_mempool_is_mineable() -> None:
    client, cfg, _ = new_test_client()
    cbor_tx, tx_hash, sender = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=0)
    raw = cbor_tx

    mine_fund = rpc_call(client, "miner.mine", {"count": 5, "address": sender})["result"]
    assert mine_fund["mined"] == 5

    from rpc.methods import tx as tx_methods

    _decoded, obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
    enriched = dict(obj)

    ctx = deps.get_ctx()
    ctx.mempool.submit(tx=enriched, raw=raw, tx_hash_hex=tx_hash)

    pending = rpc_call(client, "mempool.getPending", params={})["result"]
    assert tx_hash in pending

    mine_resp = rpc_call(client, "miner.mine", {"count": 1, "address": sender})["result"]
    assert mine_resp["mined"] == 1
    height = mine_resp["height"]

    block = rpc_call(client, "chain.getBlockByNumber", [height, True])["result"]
    assert block is not None
    txs = block.get("transactions", [])
    tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in txs]

    assert tx_hash in tx_hashes

    pending_after = rpc_call(client, "mempool.getPending", params={})["result"]
    assert tx_hash not in pending_after
