from __future__ import annotations

import asyncio

from rpc.methods import ptl as ptl_methods
from rpc.methods import receipt as receipt_methods
from rpc.methods import tx as tx_methods


def test_tx_get_accepts_scalar_hash_without_attribute_error(
    monkeypatch,
) -> None:
    tx_hash = "0x" + ("00" * 32)

    monkeypatch.setattr(ptl_methods, "_get_ptl_service", lambda: None)

    monkeypatch.setattr(tx_methods, "tx_get_transaction_by_hash", lambda _tx_hash: None)
    monkeypatch.setattr(
        tx_methods,
        "tx_get_transaction_status",
        lambda _tx_hash: {"hash": tx_hash, "status": "not_found"},
    )

    out = asyncio.run(ptl_methods.tx_get(tx_hash))
    assert out is None


def test_tx_get_receipt_unknown_pending_confirmed_shapes(
    monkeypatch,
) -> None:
    tx_hash = "0x" + ("11" * 32)

    # Unknown receipt -> null
    monkeypatch.setattr(receipt_methods, "_pending_contains", lambda _h: False)
    monkeypatch.setattr(receipt_methods, "_lookup_receipt_loc", lambda _h: None)
    assert receipt_methods.tx_get_transaction_receipt(tx_hash) is None

    # Pending receipt -> null
    monkeypatch.setattr(receipt_methods, "_pending_contains", lambda _h: True)
    assert receipt_methods.tx_get_transaction_receipt(tx_hash) is None

    # Confirmed receipt -> normalized dict
    monkeypatch.setattr(receipt_methods, "_pending_contains", lambda _h: False)
    monkeypatch.setattr(
        receipt_methods,
        "_lookup_receipt_loc",
        lambda _h: {"height": 12, "index": 0, "block_hash": b"\xaa" * 32},
    )
    monkeypatch.setattr(
        receipt_methods,
        "_fetch_block_and_receipt",
        lambda _loc, _hash: (
            {"hash": b"\xaa" * 32},
            {
                "status": 1,
                "gasUsed": 21_000,
                "logs": [],
                "contractAddress": "0x" + ("22" * 32),
            },
        ),
    )

    confirmed = receipt_methods.tx_get_transaction_receipt(tx_hash)
    assert isinstance(confirmed, dict)
    assert confirmed.get("transactionHash") == tx_hash
    assert confirmed.get("blockNumber") == 12
    assert confirmed.get("blockHash") == "0x" + ("aa" * 32)
    assert confirmed.get("contractAddress") == "0x" + ("22" * 32)
