from __future__ import annotations

import json
from pathlib import Path

import pytest

from animica.cli import tx as tx_cli
from animica.qt_wallet_bridge import (
    _format_rpc_submit_error,
    create_wallet,
    export_secret,
    fetch_history,
    init_store,
    import_wallets,
    list_wallets,
    preview_contract_call,
    rename_wallet,
    set_default,
    supported_algorithms,
    validate_wallet_address,
)


def test_supported_algorithms_include_required_wallet_schemes() -> None:
    result = supported_algorithms()
    names = {item["name"] for item in result["algorithms"]}
    assert "dilithium3" in names
    assert "sphincs_shake_128s" in names


def test_wallet_lifecycle_export_import_and_validation(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    imported_wallet_file = tmp_path / "imported-wallets.json"
    export_file = tmp_path / "wallet-export.json"

    init_store(str(wallet_file))
    alpha = create_wallet(str(wallet_file), "Alpha", "dilithium3")["wallet"]
    beta = create_wallet(str(wallet_file), "Beta", "sphincs_shake_128s")["wallet"]

    renamed = rename_wallet(str(wallet_file), alpha["wallet_id"], "Treasury")["wallet"]
    assert renamed["label"] == "Treasury"

    default_wallet = set_default(str(wallet_file), beta["wallet_id"])["wallet"]
    assert default_wallet["wallet_id"] == beta["wallet_id"]
    assert default_wallet["is_default"] is True

    listing = list_wallets(str(wallet_file))
    assert {wallet["label"] for wallet in listing["wallets"]} == {"Treasury", "Beta"}
    assert listing["default"] == "Beta"

    export_secret(str(wallet_file), beta["wallet_id"], str(export_file))
    assert export_file.exists()

    init_store(str(imported_wallet_file))
    import_wallets(str(imported_wallet_file), str(export_file), "merge")
    imported = list_wallets(str(imported_wallet_file))
    assert len(imported["wallets"]) == 1
    assert imported["wallets"][0]["address"] == beta["address"]

    assert validate_wallet_address(beta["address"])["valid"] is True
    assert validate_wallet_address("not-an-address")["valid"] is False


def test_fetch_history_reads_pending_records_from_wallet_store(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    init_store(str(wallet_file))
    wallet = create_wallet(str(wallet_file), "History Wallet", "dilithium3")["wallet"]

    payload = json.loads(wallet_file.read_text(encoding="utf-8"))
    payload["wallets"][0]["pending_txs"] = [
        {
            "tx_hash": "0xabc123",
            "from": wallet["address"],
            "to": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
            "value": 1_250_000_000,
            "fee_reserved": 25_000_000,
            "reserve_amount": 1_275_000_000,
            "nonce": 7,
            "chain_id": 1337,
            "status": "mempool_accepted",
            "created_at": "2026-04-08T10:00:00Z",
            "updated_at": "2026-04-08T10:05:00Z",
        }
    ]
    wallet_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    history = fetch_history(str(wallet_file), rpc_url=None, explorer_url=None, wallet_id=wallet["wallet_id"])
    assert history["sources"]["pending"] == "ok"
    assert history["sources"]["explorer"] == "unavailable"
    assert len(history["items"]) == 1

    item = history["items"][0]
    assert item["hash"] == "0xabc123"
    assert item["direction"] == "outgoing"
    assert item["status"] == "mempool_accepted"
    assert item["amount"] == 1_250_000_000
    assert item["fee"] == 25_000_000
    assert item["counterparty"] == "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def test_preview_contract_call_builds_payload_for_valid_wallet_address(tmp_path: Path) -> None:
    wallet_file = tmp_path / "wallets.json"
    init_store(str(wallet_file))
    wallet = create_wallet(str(wallet_file), "Contracts", "dilithium3")["wallet"]

    abi = [
        {
            "type": "function",
            "name": "inc",
            "inputs": [{"name": "value", "type": "uint64"}],
            "outputs": [],
            "stateMutability": "nonpayable",
        }
    ]

    preview = preview_contract_call(wallet["address"], abi, "inc", [7])
    assert preview["payload"].startswith("0x")
    assert len(preview["payload"]) > 2

    with pytest.raises(ValueError):
        preview_contract_call("not-an-address", abi, "inc", [7])


def test_format_rpc_submit_error_prefers_mempool_reason_and_context() -> None:
    exc = tx_cli.RpcError(
        code=-32010,
        message="mempool admission failed",
        data={
            "mempoolError": {
                "reason_code": "nonce_gap",
                "message": "mempool admission failed: nonce_gap",
                "hint": "Submit missing lower nonce transactions first.",
                "context": {"expected_nonce": 4, "got_nonce": 7},
            }
        },
    )

    out = _format_rpc_submit_error(exc)
    assert "transaction rejected by node: nonce_gap" in out
    assert "hint=Submit missing lower nonce transactions first." in out
    assert "expected_nonce=4 got_nonce=7" in out


def test_format_rpc_submit_error_falls_back_to_rpc_error() -> None:
    exc = tx_cli.RpcError(code=-32601, message="Method not found", data=None)
    assert _format_rpc_submit_error(exc) == "rpc error -32601: Method not found"
