from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rpc import errors as rpc_errors
from rpc.methods import wallet as wallet_methods

FROM_ADDRESS = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
TO_ADDRESS = "anim1zqp2u7fz3msky532tz4d3076wm99datq9rdxqjxvznq7zqn7xj0869ctuj4km"


class _Ctx:
    client = ("127.0.0.1", 12345)
    headers = {}


def test_wallet_rpc_auth_allows_docker_bridge_but_rejects_public_ip() -> None:
    wallet_methods._authorize_wallet_rpc(SimpleNamespace(client=("172.18.0.1", 50000), headers={}))
    with pytest.raises(rpc_errors.AccessDenied):
        wallet_methods._authorize_wallet_rpc(SimpleNamespace(client=("8.8.8.8", 50000), headers={}))


def test_wallet_create_address_uses_cli_store_without_returning_secret(monkeypatch, tmp_path) -> None:
    wallet_file = tmp_path / "wallets.json"
    entry = wallet_methods.wallet_cli.WalletEntry(
        label="ANM-test-user",
        address=FROM_ADDRESS,
        alg_id=0x1001,
        alg_name="dilithium3",
        public_key_hex="aa",
        secret_key_hex="bb",
        created_at="2026-05-02T00:00:00Z",
    )

    monkeypatch.setattr(wallet_methods.wallet_cli, "HAVE_PQ", False)
    monkeypatch.setattr(
        wallet_methods.wallet_cli,
        "_resolve_signature_alg",
        lambda _alg: SimpleNamespace(alg_id=0x1001, name="dilithium3"),
    )
    monkeypatch.setattr(wallet_methods.wallet_cli, "_generate_entry", lambda *_, **__: entry)

    result = wallet_methods.wallet_create_address(
        label="ANM-test-user",
        wallet_file=str(wallet_file),
        ctx=_Ctx(),
    )

    assert result["address"] == FROM_ADDRESS
    assert result["label"] == "ANM-test-user"
    assert "secret_key_hex" not in result

    stored = json.loads(wallet_file.read_text(encoding="utf-8"))
    assert stored["default_address"] == FROM_ADDRESS
    assert stored["wallets"][0]["secret_key_hex"] == "bb"

    with pytest.raises(rpc_errors.AlreadyExists):
        wallet_methods.wallet_create_address(
            label="ANM-test-user",
            wallet_file=str(wallet_file),
            ctx=_Ctx(),
        )


def test_head_height_prefers_active_height_when_canonical_diverges(monkeypatch) -> None:
    monkeypatch.setattr(
        wallet_methods.chain_methods,
        "chain_get_head",
        lambda: {"height": 10320, "number": 10320, "canonicalHeight": 18877},
    )

    assert wallet_methods._head_height() == 10320


def test_head_height_falls_back_to_canonical_when_active_height_is_zero(monkeypatch) -> None:
    monkeypatch.setattr(
        wallet_methods.chain_methods,
        "chain_get_head",
        lambda: {"height": 0, "canonicalHeight": 18877},
    )

    assert wallet_methods._head_height() == 18877


def test_locked_store_preserves_frozen_rpc_errors(tmp_path) -> None:
    wallet_file = tmp_path / "wallets.json"
    wallet_file.write_text(
        json.dumps({"format": "animica.wallets", "version": 2, "wallets": []}),
        encoding="utf-8",
    )

    with pytest.raises(rpc_errors.InvalidTx) as exc_info:
        with wallet_methods._locked_store(wallet_file):
            raise rpc_errors.InvalidTx("expired", current=18877)

    assert exc_info.value.message == "expired"
    assert exc_info.value.data == {"current": 18877}


def test_wallet_send_accepts_cex_object_payload_and_returns_txid(monkeypatch, tmp_path) -> None:
    wallet_file = tmp_path / "wallets.json"
    wallet_file.write_text(
        json.dumps(
            {
                "format": "animica.wallets",
                "version": 2,
                "default": "hot",
                "default_address": FROM_ADDRESS,
                "wallets": [
                    {
                        "label": "hot",
                        "address": FROM_ADDRESS,
                        "alg_id": 0x1001,
                        "alg_name": "dilithium3",
                        "public_key_hex": "aa",
                        "secret_key_hex": "bb",
                        "created_at": "2026-05-02T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(wallet_methods.chain_methods, "chain_get_chain_identity", lambda: {"chainId": 1})
    monkeypatch.setenv("ANIMICA_WALLET_RPC_TTL_BLOCKS", "120")
    monkeypatch.setenv("ANIMICA_WALLET_RPC_VALID_AFTER_LAG_BLOCKS", "5")
    monkeypatch.setenv("ANIMICA_MAX_TX_TTL_BLOCKS", "200")
    monkeypatch.setattr(
        wallet_methods.chain_methods,
        "chain_get_head",
        lambda: {"height": 12, "canonicalHeight": 9999},
    )
    monkeypatch.setattr(wallet_methods.state_methods, "state_get_next_nonce", lambda _address: 7)
    monkeypatch.setattr(wallet_methods.tx_cli, "_address_to_32_bytes", lambda _address: b"\x00" * 32)
    monkeypatch.setattr(
        wallet_methods.tx_cli,
        "_chain_context_from_identity",
        lambda *_args, **_kwargs: SimpleNamespace(domain="tx", prehash="sha3-512"),
    )
    monkeypatch.setattr(wallet_methods.tx_cli, "pq_sign_tx", lambda *_args, **_kwargs: SimpleNamespace(alg_id=0x1001, sig=b"sig"))
    monkeypatch.setattr(wallet_methods.tx_cli, "pq_verify_tx", lambda *_args, **_kwargs: SimpleNamespace(ok=True))
    monkeypatch.setattr(wallet_methods.tx_cli, "_build_raw_tx", lambda *_args, **_kwargs: b"raw")
    monkeypatch.setattr(wallet_methods.tx_methods, "tx_send_raw_transaction", lambda _raw: {"tx_hash": "0xabc"})

    result = wallet_methods.wallet_send(
        {
            "to": TO_ADDRESS,
            "amountAtoms": "1000",
            "feeAtoms": "100000",
            "label": "hot",
        },
        wallet_file=str(wallet_file),
        ctx=_Ctx(),
    )

    assert result["txid"] == "0xabc"
    assert result["from"] == FROM_ADDRESS
    assert result["to"] == TO_ADDRESS
    assert result["amountAtoms"] == "1000"
    assert result["feeAtoms"] == "100000"
    assert result["gasLimit"] == 21000
    assert result["maxFee"] == 5
    assert result["validAfter"] == 7
    assert result["validUntil"] == 132
