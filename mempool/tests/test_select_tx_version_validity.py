from __future__ import annotations

from mempool.select import PendingTxEntry, select_for_block


def test_select_for_block_accepts_v1_nonce_tx_without_validity_window() -> None:
    sender = b"\x11" * 32
    tx = {
        "body": {
            "v": 1,
            "chainId": 1,
            "from": sender,
            "nonce": 0,
            "gasLimit": 21000,
            "maxFee": 1,
            "value": 1,
        }
    }
    entry = PendingTxEntry(hash_hex="0x" + "22" * 32, raw=b"", tx=tx)

    selection = select_for_block(
        head_state={"chain_id": 1, "height": 5},
        limits={"max_gas": 1_000_000, "max_bytes": 1_000_000, "max_txs": 10},
        pending=[entry],
        decode=None,
        state_db=None,
        policy={"min_gas_price": 0},
        tx_index=None,
        signature_validator=None,
    )

    assert selection.selected_hashes == [entry.hash_hex]


def test_select_for_block_rejects_v2_tx_missing_validity_window() -> None:
    sender = b"\x33" * 32
    tx = {
        "body": {
            "v": 2,
            "chainId": 1,
            "from": sender,
            "gasLimit": 21000,
            "maxFee": 1,
            "value": 1,
        }
    }
    entry = PendingTxEntry(hash_hex="0x" + "44" * 32, raw=b"", tx=tx)

    selection = select_for_block(
        head_state={"chain_id": 1, "height": 5},
        limits={"max_gas": 1_000_000, "max_bytes": 1_000_000, "max_txs": 10},
        pending=[entry],
        decode=None,
        state_db=None,
        policy={"min_gas_price": 0},
        tx_index=None,
        signature_validator=None,
    )

    assert selection.rejected_by_hash[entry.hash_hex] == "missing_validity"
