from __future__ import annotations

from dataclasses import replace

import pytest

from core.encoding.canonical import tx_sign_bytes
from core.genesis.loader import compute_chain_identity
from core.types.header import Header, serialize_header
from core.types.tx import PqSignature, Tx, TxKind, TxTransfer, UnsignedTx
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


def _header_from_template(header_view: dict) -> Header:
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


def _find_nonce(header: Header, target_int: int, max_nonce: int = 100000) -> tuple[int, bytes]:
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


def test_template_includes_mempool_txs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")

    client, cfg, _tmp = new_test_client()
    sender_kp = keygen_sig("dilithium3")
    recipient_kp = keygen_sig("dilithium3")

    sender_bytes = _address_bytes(sender_kp.address)
    sender_hex = "0x" + sender_bytes.hex()
    recipient_bytes = _address_bytes(recipient_kp.address)
    recipient_hex = "0x" + recipient_bytes.hex()

    template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": sender_kp.address, "include_mempool": False},
    )["result"]
    header = _header_from_template(template["header"])
    target_int = int(template["target"], 16)
    nonce, _digest = _find_nonce(header, target_int)
    header = replace(header, nonce=nonce)
    header_payload = {
        k: ("0x" + v.hex() if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header.to_obj().items()
    }
    block_payload = {
        "header": header_payload,
        "txs": [],
        "proofs": [],
        "parentHash": template["parent"]["hash"],
        "templateId": template.get("templateId"),
    }
    submit = rpc_call(client, "miner.submitBlock", block_payload)["result"]
    assert submit["accepted"] is True

    sender_balance = rpc_call(client, "state.getBalance", [sender_hex])["result"]
    sender_balance = int(sender_balance, 16) if isinstance(sender_balance, str) else int(sender_balance)
    assert sender_balance > 0

    transfer_amount = 1_000_000_000
    unsigned = UnsignedTx(
        chain_id=cfg.chain_id,
        nonce=0,
        gas_price=1,
        gas_limit=21000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=transfer_amount, data=b""),
        access_list=(),
    )
    sign_bytes = tx_sign_bytes(unsigned.to_obj())
    fork_id = compute_chain_identity(None, chain_id=cfg.chain_id).fork_id
    sig_env = sign.sign_detached(
        sign_bytes,
        "dilithium3",
        sender_kp.secret_key,
        domain="tx",
        chain_id=cfg.chain_id,
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

    rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending

    template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": sender_kp.address, "include_mempool": True},
    )["result"]
    assert int(template["mempool"]["selected"]) >= 1

    header = _header_from_template(template["header"])
    target_int = int(template["target"], 16)
    nonce, _digest = _find_nonce(header, target_int)
    header = replace(header, nonce=nonce)
    header_payload = {
        k: ("0x" + v.hex() if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header.to_obj().items()
    }
    txs_raw = [tx_entry.get("raw") for tx_entry in template.get("txs", []) if isinstance(tx_entry, dict)]
    block_payload = {
        "header": header_payload,
        "txs": txs_raw,
        "proofs": [],
        "parentHash": template["parent"]["hash"],
        "templateId": template.get("templateId"),
    }
    submit = rpc_call(client, "miner.submitBlock", block_payload)["result"]
    assert submit["accepted"] is True

    head = rpc_call(client, "chain.getHead")["result"]
    block = rpc_call(client, "chain.getBlockByNumber", [head["height"], True])["result"]
    txs = block.get("transactions", [])
    tx_hashes = [tx.get("hash") if isinstance(tx, dict) else tx for tx in txs]
    assert tx_hash in tx_hashes

    pending_after = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash not in pending_after

    recipient_balance = rpc_call(client, "state.getBalance", [recipient_hex])["result"]
    recipient_balance = int(recipient_balance, 16) if isinstance(recipient_balance, str) else int(recipient_balance)
    assert recipient_balance >= transfer_amount
