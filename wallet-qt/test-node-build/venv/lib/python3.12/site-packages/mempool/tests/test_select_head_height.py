from __future__ import annotations

from mempool.select import PendingTxEntry, select_for_block


def _entry(valid_after: int, valid_until: int) -> PendingTxEntry:
    sender = b"\x11" * 32
    tx = {"body": {"from": sender, "validAfter": valid_after, "validUntil": valid_until}}
    return PendingTxEntry(hash_hex="0x" + "22" * 32, raw=b"", tx=tx)


def test_select_for_block_uses_head_height_for_validity() -> None:
    entry = _entry(valid_after=3, valid_until=10)

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


def test_select_for_block_rejects_not_yet_valid_with_height_context() -> None:
    entry = _entry(valid_after=6, valid_until=10)

    selection = select_for_block(
        head_state={"chain_id": 1, "height": 2},
        limits={"max_gas": 1_000_000, "max_bytes": 1_000_000, "max_txs": 10},
        pending=[entry],
        decode=None,
        state_db=None,
        policy={"min_gas_price": 0},
        tx_index=None,
        signature_validator=None,
    )

    assert entry.hash_hex in selection.rejected_by_hash
    assert selection.rejected_by_hash[entry.hash_hex] == "not_yet_valid"
    details = selection.rejected_details_by_hash[entry.hash_hex]["details"]
    assert details["current_height"] == 2
