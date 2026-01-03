from __future__ import annotations

import pytest

from rpc.tests import new_test_client, rpc_call
from rpc.tests.test_tx_flow import _build_signed_transfer_cbor


pytestmark = pytest.mark.anyio


def test_state_nonce_and_pending_nonce_flow() -> None:
    client, cfg, _ = new_test_client()

    cbor_tx, tx_hash, sender = _build_signed_transfer_cbor(cfg.chain_id, from_nonce=0)
    raw_hex = "0x" + cbor_tx.hex()

    initial = rpc_call(client, "state.getNonce", [sender])["result"]
    assert initial == 0

    # Fund sender so tx can be accepted into the mempool.
    rpc_call(client, "miner.mine", {"count": 1, "address": sender})

    submit = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert submit["result"] == tx_hash

    pending_next = rpc_call(client, "state.getNextNonce", [sender])["result"]
    assert pending_next == 1

    pending_tag = rpc_call(client, "state.getNonce", [sender, "pending"])["result"]
    assert pending_tag == 1

    # Mine a block so confirmed nonce advances.
    rpc_call(client, "miner.mine", {"count": 1, "address": sender})

    confirmed = rpc_call(client, "state.getNonce", [sender])["result"]
    assert confirmed == 1
