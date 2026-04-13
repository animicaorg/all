from __future__ import annotations

import asyncio

from rpc.methods import block as block_methods
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


def test_tx_get_receipt_includes_deploy_metadata_from_index(monkeypatch) -> None:
    tx_hash = "0x" + ("33" * 32)
    contract = "0x" + ("44" * 32)

    monkeypatch.setattr(receipt_methods, "_pending_contains", lambda _h: False)
    monkeypatch.setattr(
        receipt_methods,
        "_lookup_receipt_loc",
        lambda _h: {"height": 2, "index": 1, "block_hash": b"\xbb" * 32},
    )
    monkeypatch.setattr(
        receipt_methods,
        "_fetch_block_and_receipt",
        lambda _loc, _hash: ({"hash": b"\xbb" * 32}, {"status": None, "gasUsed": 22000, "logs": []}),
    )
    monkeypatch.setattr(
        receipt_methods,
        "_lookup_deploy_metadata",
        lambda _tx_hash: {
            "contractAddress": contract,
            "deploymentType": "python_vm_package",
            "codeHash": "0x" + ("55" * 32),
            "manifestHash": "0x" + ("66" * 32),
            "status": 1,
        },
    )

    out = receipt_methods.tx_get_transaction_receipt(tx_hash)
    assert isinstance(out, dict)
    assert out.get("contractAddress") == contract
    assert out.get("createdAddress") == contract
    assert out.get("deploymentType") == "python_vm_package"
    assert out.get("codeHash") == "0x" + ("55" * 32)
    assert out.get("manifestHash") == "0x" + ("66" * 32)
    assert out.get("status") == 1


def test_tx_view_includes_deploy_metadata(monkeypatch) -> None:
    tx_hash = "0x" + ("77" * 32)
    contract = "0x" + ("88" * 32)

    monkeypatch.setattr(
        tx_methods,
        "_lookup_deploy_metadata",
        lambda _tx_hash_hex: {
            "contractAddress": contract,
            "deploymentType": "python_vm_package",
            "codeHash": "0x" + ("99" * 32),
            "manifestHash": "0x" + ("aa" * 32),
        },
    )

    tx_obj = {
        "hash": tx_hash,
        "from": "0x" + ("11" * 32),
        "payload": {"v": {"code": b"code", "manifest": b"manifest"}},
        "kind": 1,
    }
    view = tx_methods._tx_view(
        tx_obj,
        tx_obj,
        pending=False,
        block_hash=bytes.fromhex("bb" * 32),
        block_number=4,
        tx_index=0,
    )
    assert view.get("contractAddress") == contract
    assert view.get("createdAddress") == contract
    assert view.get("deploymentType") == "python_vm_package"
    assert view.get("codeHash") == "0x" + ("99" * 32)
    assert view.get("manifestHash") == "0x" + ("aa" * 32)


def test_block_view_tx_objects_include_deploy_metadata_and_transfer_unchanged(monkeypatch) -> None:
    from types import SimpleNamespace

    deploy_hash = "0x" + ("10" * 32)
    transfer_hash = "0x" + ("20" * 32)
    contract = "0x" + ("30" * 32)

    # Force deterministic tx hashes for this unit test.
    monkeypatch.setattr(
        block_methods,
        "_compute_tx_hash",
        lambda tx: tx.get("hash") if isinstance(tx, dict) else None,
    )
    monkeypatch.setattr(
        block_methods,
        "_lookup_deploy_metadata_by_hash",
        lambda h: {
            "contractAddress": contract,
            "deploymentType": "python_vm_package",
            "codeHash": "0x" + ("40" * 32),
            "manifestHash": "0x" + ("50" * 32),
        }
        if h == deploy_hash
        else None,
    )

    block = SimpleNamespace(
        header=SimpleNamespace(
            hash="0x" + ("aa" * 32),
            parentHash="0x" + ("bb" * 32),
            timestamp=123,
            chainId=1,
            stateRoot="0x" + ("01" * 32),
            txsRoot="0x" + ("02" * 32),
            receiptsRoot="0x" + ("03" * 32),
        ),
        txs=[
            {"hash": transfer_hash, "from": "0x" + ("01" * 32), "to": "0x" + ("02" * 32), "kind": 0, "value": 1},
            {"hash": deploy_hash, "from": "0x" + ("03" * 32), "to": None, "kind": 1, "payload": {"v": {"code": b"x", "manifest": b"y"}}},
        ],
        receipts=[],
    )

    out = block_methods._block_view(
        block,
        2,
        include_txs=True,
        include_receipts=False,
        chain_id_fallback=1,
    )
    txs = out.get("transactions") or []
    assert len(txs) == 2
    transfer_view, deploy_view = txs
    assert "contractAddress" not in transfer_view
    assert "deploymentType" not in transfer_view
    assert deploy_view.get("contractAddress") == contract
    assert deploy_view.get("deploymentType") == "python_vm_package"
