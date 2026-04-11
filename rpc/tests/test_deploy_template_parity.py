from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from core.encoding.canonical import tx_sign_bytes
from core.genesis.loader import compute_chain_identity
from core.types.header import Header, serialize_header
from core.types.tx import PqSignature, Tx, TxDeploy, TxKind, TxTransfer, UnsignedTx
from core.utils.hash import sha3_256
from pq.py import sign
from pq.py.address import decode_address
from pq.py.keygen import keygen_sig
from pq.py.registry import ALG_ID
from rpc.tests import new_test_client, rpc_call


def _parse_hex_bytes(value: str) -> bytes:
    hex_value = value[2:] if value.startswith("0x") else value
    if len(hex_value) % 2:
        hex_value = "0" + hex_value
    return bytes.fromhex(hex_value)


def _header_from_template(header_view: dict[str, Any]) -> Header:
    return Header(
        v=int(header_view.get("v", 1)),
        chainId=int(header_view.get("chainId", header_view.get("chain_id", 0))),
        height=int(header_view.get("height", header_view.get("number", 0))),
        parentHash=_parse_hex_bytes(header_view["parentHash"]),
        timestamp=int(header_view.get("timestamp", 0)),
        stateRoot=_parse_hex_bytes(header_view.get("stateRoot", "0x" + "00" * 32)),
        txsRoot=_parse_hex_bytes(header_view.get("txsRoot", "0x" + "00" * 32)),
        receiptsRoot=_parse_hex_bytes(
            header_view.get("receiptsRoot", "0x" + "00" * 32)
        ),
        proofsRoot=_parse_hex_bytes(header_view.get("proofsRoot", "0x" + "00" * 32)),
        daRoot=_parse_hex_bytes(header_view.get("daRoot", "0x" + "00" * 32)),
        mixSeed=_parse_hex_bytes(header_view.get("mixSeed", "0x" + "00" * 32)),
        poiesPolicyRoot=_parse_hex_bytes(
            header_view.get("poiesPolicyRoot", "0x" + "00" * 32)
        ),
        pqAlgPolicyRoot=_parse_hex_bytes(
            header_view.get("pqAlgPolicyRoot", "0x" + "00" * 32)
        ),
        thetaMicro=int(header_view.get("thetaMicro", 0)),
        workType=int(header_view.get("workType", 0)),
        nonce=int(header_view.get("nonce", 0)),
        extra=_parse_hex_bytes(header_view.get("extra", "0x")),
    )


def _find_nonce(
    header: Header, target_int: int, max_nonce: int = 100_000
) -> tuple[int, bytes]:
    for nonce in range(max_nonce):
        candidate = replace(header, nonce=nonce)
        digest = sha3_256(serialize_header(candidate))
        if int.from_bytes(digest, "big") <= target_int:
            return nonce, digest
    pytest.skip("could not find valid nonce within search space")


def _address_bytes(address: str) -> bytes:
    record = decode_address(address)
    digest = bytes(record.digest) if isinstance(record.digest, list) else record.digest
    return digest[:32].ljust(32, b"\x00")


def _build_signed_deploy_tx(
    *,
    chain_id: int,
    sender_kp: Any,
    version: int,
    gas_limit: int,
    max_fee: int,
    nonce: int | None = None,
    valid_after: int | None = None,
    valid_until: int | None = None,
    salt: bytes | None = None,
) -> tuple[Tx, str, str]:
    sender = _address_bytes(sender_kp.address)
    payload = TxDeploy(
        code=b"def _entry():\n    return 1\n",
        manifest=json.dumps({"name": "TestDeploy", "version": "1.0"}).encode("utf-8"),
    )
    unsigned = UnsignedTx(
        version=int(version),
        chain_id=int(chain_id),
        fork_id=None,
        valid_after=int(valid_after) if valid_after is not None else None,
        valid_until=int(valid_until) if valid_until is not None else None,
        salt=bytes(salt) if salt is not None else None,
        gas_price=int(max_fee),
        gas_limit=int(gas_limit),
        sender=sender,
        kind=TxKind.DEPLOY,
        payload=payload,
        access_list=(),
        nonce=int(nonce) if nonce is not None else None,
    )
    sign_bytes = tx_sign_bytes(unsigned.to_obj())
    fork_id = compute_chain_identity(None, chain_id=chain_id).fork_id
    sig_env = sign.sign_detached(
        sign_bytes,
        "dilithium3",
        sender_kp.secret_key,
        domain="tx",
        chain_id=chain_id,
        fork_id=fork_id,
    )
    sig = PqSignature(
        alg_id=ALG_ID["dilithium3"],
        pubkey=sender_kp.public_key,
        sig=sig_env.sig,
    )
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    raw_hex = "0x" + tx.to_cbor().hex()
    tx_hash = "0x" + tx.txid().hex()
    return tx, raw_hex, tx_hash


def _build_signed_transfer_tx(
    *,
    chain_id: int,
    sender_kp: Any,
    version: int,
    gas_limit: int,
    max_fee: int,
    amount: int,
    to: bytes,
    data: bytes,
    nonce: int | None = None,
    valid_after: int | None = None,
    valid_until: int | None = None,
    salt: bytes | None = None,
) -> tuple[Tx, str, str]:
    sender = _address_bytes(sender_kp.address)
    payload = TxTransfer(to=to, amount=int(amount), data=bytes(data))
    unsigned = UnsignedTx(
        version=int(version),
        chain_id=int(chain_id),
        fork_id=None,
        valid_after=int(valid_after) if valid_after is not None else None,
        valid_until=int(valid_until) if valid_until is not None else None,
        salt=bytes(salt) if salt is not None else None,
        gas_price=int(max_fee),
        gas_limit=int(gas_limit),
        sender=sender,
        kind=TxKind.TRANSFER,
        payload=payload,
        access_list=(),
        nonce=int(nonce) if nonce is not None else None,
    )
    sign_bytes = tx_sign_bytes(unsigned.to_obj())
    fork_id = compute_chain_identity(None, chain_id=chain_id).fork_id
    sig_env = sign.sign_detached(
        sign_bytes,
        "dilithium3",
        sender_kp.secret_key,
        domain="tx",
        chain_id=chain_id,
        fork_id=fork_id,
    )
    sig = PqSignature(
        alg_id=ALG_ID["dilithium3"],
        pubkey=sender_kp.public_key,
        sig=sig_env.sig,
    )
    tx = Tx(unsigned=unsigned, sigs=(sig,))
    raw_hex = "0x" + tx.to_cbor().hex()
    tx_hash = "0x" + tx.txid().hex()
    return tx, raw_hex, tx_hash


def _submit_template_block(client, template: dict[str, Any]) -> dict[str, Any]:
    header = _header_from_template(template["header"])
    target_int = int(template["target"], 16)
    nonce, _digest = _find_nonce(header, target_int)
    header = replace(header, nonce=nonce)
    header_payload = {
        k: ("0x" + v.hex() if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header.to_obj().items()
    }
    txs_raw = [
        tx_entry.get("raw")
        for tx_entry in template.get("txs", [])
        if isinstance(tx_entry, dict)
    ]
    block_payload = {
        "header": header_payload,
        "txs": txs_raw,
        "proofs": [],
        "parentHash": template["parent"]["hash"],
        "templateId": template.get("templateId"),
    }
    return rpc_call(client, "miner.submitBlock", block_payload)["result"]


def test_valid_deploy_included_and_block_apply_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")
    monkeypatch.setenv("ANIMICA_ASSERT_BALANCE", "1")

    client, cfg, _tmp = new_test_client()
    sender_kp = keygen_sig("dilithium3")

    # Fund sender from coinbase.
    funding_template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": sender_kp.address, "include_mempool": False},
    )["result"]
    funding_submit = _submit_template_block(client, funding_template)
    assert funding_submit["accepted"] is True

    head_before = rpc_call(client, "chain.getHead")["result"]
    current_height = int(head_before.get("height", 0))
    _tx, raw_hex, tx_hash = _build_signed_deploy_tx(
        chain_id=cfg.chain_id,
        sender_kp=sender_kp,
        version=2,
        gas_limit=60_000,
        max_fee=1,
        valid_after=current_height,
        valid_until=current_height + 30,
        salt=b"\x11" * 16,
    )
    rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})

    template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": sender_kp.address, "include_mempool": True},
    )["result"]
    selected_hashes = [tx.get("hash") for tx in template.get("txs", []) if isinstance(tx, dict)]
    assert tx_hash in selected_hashes
    assert int(template.get("mempool", {}).get("selected", 0)) >= 1

    submit = _submit_template_block(client, template)
    assert submit["accepted"] is True

    receipt = rpc_call(client, "tx.getReceipt", [tx_hash])["result"]
    assert receipt is not None


def test_expired_deploy_rejected_before_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")

    client, cfg, _tmp = new_test_client()
    sender_kp = keygen_sig("dilithium3")

    head = rpc_call(client, "chain.getHead")["result"]
    current_height = int(head.get("height", 0))
    _tx, raw_hex, tx_hash = _build_signed_deploy_tx(
        chain_id=cfg.chain_id,
        sender_kp=sender_kp,
        version=2,
        gas_limit=60_000,
        max_fee=1,
        valid_after=max(0, current_height - 10),
        valid_until=max(0, current_height - 1),
        salt=b"\x22" * 16,
    )

    err = rpc_call(
        client,
        "tx.sendRawTransaction",
        {"rawTx": raw_hex},
        expect_error=True,
    )["error"]
    err_blob = json.dumps(err).lower()
    assert "expired" in err_blob or "valid_until" in err_blob

    template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": sender_kp.address, "include_mempool": True},
    )["result"]
    selected_hashes = [tx.get("hash") for tx in template.get("txs", []) if isinstance(tx, dict)]
    assert tx_hash not in selected_hashes


def test_nonce_gap_deploy_rejected_on_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")

    client, cfg, _tmp = new_test_client()
    sender_kp = keygen_sig("dilithium3")

    _tx, raw_hex, _tx_hash = _build_signed_deploy_tx(
        chain_id=cfg.chain_id,
        sender_kp=sender_kp,
        version=1,
        gas_limit=60_000,
        max_fee=1,
        nonce=9,
    )
    err = rpc_call(
        client,
        "tx.sendRawTransaction",
        {"rawTx": raw_hex},
        expect_error=True,
    )["error"]
    err_blob = json.dumps(err).lower()
    assert "nonce_gap" in err_blob or "nonce" in err_blob


def test_zero_recipient_transfer_rejected_before_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")

    client, cfg, _tmp = new_test_client()
    sender_kp = keygen_sig("dilithium3")

    head = rpc_call(client, "chain.getHead")["result"]
    current_height = int(head.get("height", 0))
    _tx, raw_hex, tx_hash = _build_signed_transfer_tx(
        chain_id=cfg.chain_id,
        sender_kp=sender_kp,
        version=2,
        gas_limit=60_000,
        max_fee=1,
        amount=0,
        to=b"\x00" * 32,
        data=b"deploy-like payload",
        valid_after=current_height,
        valid_until=current_height + 30,
        salt=b"\x33" * 16,
    )
    err = rpc_call(
        client,
        "tx.sendRawTransaction",
        {"rawTx": raw_hex},
        expect_error=True,
    )["error"]
    err_blob = json.dumps(err).lower()
    assert "invalid_recipient" in err_blob

    template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": sender_kp.address, "include_mempool": True},
    )["result"]
    selected_hashes = [tx.get("hash") for tx in template.get("txs", []) if isinstance(tx, dict)]
    assert tx_hash not in selected_hashes
