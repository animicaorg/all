from __future__ import annotations

from mempool.select import PendingTxEntry, select_for_block


class _State:
    def get_balance(self, _addr: bytes) -> int:
        return 10_000_000_000


def test_select_rejects_transfer_with_zero_recipient() -> None:
    tx = {
        "tx": {
            "v": 2,
            "chainId": 1,
            "from": b"\x11" * 32,
            "gas": {"price": 1, "limit": 21_000},
            "payload": {
                "t": 0,
                "v": {"to": b"\x00" * 32, "amount": 0, "data": b""},
            },
            "validAfter": 1,
            "validUntil": 100,
            "salt": b"\x22" * 16,
            "accessList": [],
        },
        "sigs": [],
    }
    pending = [
        PendingTxEntry(
            hash_hex="0x" + "ab" * 32,
            raw=b"",
            tx=tx,
        )
    ]

    selection = select_for_block(
        head_state={"height": 5},
        limits={"max_gas": 1_000_000, "max_bytes": 1_000_000, "max_txs": 100},
        pending=pending,
        state_db=_State(),
    )

    assert selection.selected == []
    assert selection.rejected.get("invalid_recipient") == 1
    details = selection.rejected_details_by_hash.get("0x" + "ab" * 32, {})
    assert details.get("reason") == "invalid_recipient"
    assert details.get("details", {}).get("stage") == "template_filter"
