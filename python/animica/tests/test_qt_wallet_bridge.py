from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import animica.qt_wallet_bridge as bridge
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


def test_submit_signed_tx_uses_string_rpc_endpoint_when_resolver_returns_tuple(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from_address = "anim1fromqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
    to_address = "anim1toqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
    store = {
        "wallets": [
            {
                "address": from_address,
                "public_key_hex": "aa",
                "secret_key_hex": "bb",
                "alg_id": 0x1001,
            }
        ]
    }

    monkeypatch.setattr(bridge, "_ensure_store", lambda _path: store)
    monkeypatch.setattr(bridge, "_resolve_rpc_url", lambda _rpc_url: ("http://rpc.test", "env:OMNI_RPC_URL"))
    monkeypatch.setattr(
        bridge,
        "wallet_overview",
        lambda *_args, **_kwargs: {"wallets": [{"address": from_address, "balance_available": 10_000_000_000}]},
    )
    monkeypatch.setattr(bridge, "_record_pending_tx", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bridge.tx_cli, "_chain_context_from_identity", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(bridge.tx_cli, "_estimate_fee_quote", lambda _rpc: (21000, 1))
    monkeypatch.setattr(bridge.tx_cli, "_get_default_max_fee", lambda _rpc: 1)
    monkeypatch.setattr(bridge.tx_cli, "_next_nonce", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(bridge.tx_cli, "_resolve_validity_window", lambda *_args, **_kwargs: (1, 100))
    monkeypatch.setattr(bridge.tx_cli, "_build_tx_body", lambda **_kwargs: b"body")
    monkeypatch.setattr(bridge.tx_cli, "_hex_to_bytes", lambda _hex: b"\x01")
    monkeypatch.setattr(
        bridge.tx_cli,
        "pq_sign_tx",
        lambda *_args, **_kwargs: SimpleNamespace(alg_id=0x1001, sig=b"\x02"),
    )
    monkeypatch.setattr(
        bridge.tx_cli,
        "pq_verify_tx",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, reason=""),
    )
    monkeypatch.setattr(bridge.tx_cli, "_build_raw_tx", lambda **_kwargs: b"\xaa")
    monkeypatch.setattr(bridge.tx_cli, "_rpc", lambda *_args, **_kwargs: "0xabc123")
    monkeypatch.setattr(bridge.tx_cli, "_get_mempool_status", lambda *_args, **_kwargs: (True, {"status": "ok"}))

    def _fake_chain_identity(rpc_endpoint: object, chain_id_override: int | None = None) -> SimpleNamespace:
        assert isinstance(rpc_endpoint, str)
        assert rpc_endpoint == "http://rpc.test"
        assert chain_id_override is None
        return SimpleNamespace(identity={"chainId": 1337})

    monkeypatch.setattr(bridge.tx_cli, "_get_chain_identity", _fake_chain_identity)

    result = bridge._submit_signed_tx(
        wallet_file=str(tmp_path / "wallets.json"),
        rpc_url=None,
        from_address=from_address,
        to_address=to_address,
        value_base=1000,
        gas_limit=21000,
        max_fee=1,
        chain_id=None,
        nonce=None,
        valid_after=None,
        valid_until=None,
        ttl_blocks=None,
        data_bytes=b"",
    )

    assert result["tx_hash"] == "0xabc123"
    assert result["chain_id"] == 1337
    assert result["auto_mine_attempted"] is False
    assert result["auto_mine_success"] is False


def test_submit_signed_tx_attempts_auto_mine_for_local_rpc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from_address = "anim1fromqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
    to_address = "anim1toqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
    store = {
        "wallets": [
            {
                "address": from_address,
                "public_key_hex": "aa",
                "secret_key_hex": "bb",
                "alg_id": 0x1001,
            }
        ]
    }

    monkeypatch.setattr(bridge, "_ensure_store", lambda _path: store)
    monkeypatch.setattr(bridge, "_resolve_rpc_url", lambda _rpc_url: ("http://127.0.0.1:8545/rpc", "cli"))
    monkeypatch.setattr(
        bridge,
        "wallet_overview",
        lambda *_args, **_kwargs: {"wallets": [{"address": from_address, "balance_available": 10_000_000_000}]},
    )
    monkeypatch.setattr(bridge, "_record_pending_tx", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bridge.tx_cli, "_chain_context_from_identity", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(bridge.tx_cli, "_estimate_fee_quote", lambda _rpc: (21000, 1))
    monkeypatch.setattr(bridge.tx_cli, "_get_default_max_fee", lambda _rpc: 1)
    monkeypatch.setattr(bridge.tx_cli, "_next_nonce", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(bridge.tx_cli, "_resolve_validity_window", lambda *_args, **_kwargs: (1, 100))
    monkeypatch.setattr(bridge.tx_cli, "_build_tx_body", lambda **_kwargs: b"body")
    monkeypatch.setattr(bridge.tx_cli, "_hex_to_bytes", lambda _hex: b"\x01")
    monkeypatch.setattr(
        bridge.tx_cli,
        "pq_sign_tx",
        lambda *_args, **_kwargs: SimpleNamespace(alg_id=0x1001, sig=b"\x02"),
    )
    monkeypatch.setattr(
        bridge.tx_cli,
        "pq_verify_tx",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, reason=""),
    )
    monkeypatch.setattr(bridge.tx_cli, "_build_raw_tx", lambda **_kwargs: b"\xaa")

    rpc_calls: list[str] = []

    def _rpc(method_rpc: str, method: str, _params: object) -> object:
        rpc_calls.append(method)
        if method == "tx.sendRawTransaction":
            return "0xabc123"
        if method == "miner.mine":
            return {"mined": 1}
        if method == "tx.getStatus":
            return {"status": "confirmed", "state": "included_block", "confirmations": 1}
        raise AssertionError(f"Unexpected RPC method: {method}")

    mempool_checks = [(True, {"status": "pending"}), (False, {"status": "not_found"})]

    def _mempool_status(*_args: object, **_kwargs: object) -> tuple[bool, dict[str, str]]:
        return mempool_checks.pop(0)

    monkeypatch.setattr(bridge.tx_cli, "_rpc", _rpc)
    monkeypatch.setattr(bridge.tx_cli, "_get_mempool_status", _mempool_status)
    monkeypatch.setattr(
        bridge.tx_cli,
        "_get_chain_identity",
        lambda _rpc_endpoint, chain_id_override=None: SimpleNamespace(identity={"chainId": 1337}),
    )

    result = bridge._submit_signed_tx(
        wallet_file=str(tmp_path / "wallets.json"),
        rpc_url=None,
        from_address=from_address,
        to_address=to_address,
        value_base=1000,
        gas_limit=21000,
        max_fee=1,
        chain_id=None,
        nonce=None,
        valid_after=None,
        valid_until=None,
        ttl_blocks=None,
        data_bytes=b"",
    )

    assert result["tx_hash"] == "0xabc123"
    assert result["auto_mine_attempted"] is True
    assert result["auto_mine_success"] is True
    assert result["wallet_record_status"] == "confirmed"
    assert rpc_calls.count("miner.mine") == 1
