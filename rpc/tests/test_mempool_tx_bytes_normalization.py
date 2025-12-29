from __future__ import annotations

import pytest

from core.encoding.canonical import tx_sign_bytes
from core.genesis.loader import compute_chain_identity
from core.types.tx import PqSignature, Tx, TxKind, TxTransfer, UnsignedTx
from pq.py import sign
from pq.py.address import decode_address
from pq.py.keygen import keygen_sig
from pq.py.registry import ALG_ID
from rpc import deps
from rpc.methods import tx as tx_methods
from rpc.tests import new_test_client, rpc_call


def _address_bytes(address: str) -> bytes:
    record = decode_address(address)
    digest = bytes(record.digest) if isinstance(record.digest, list) else record.digest
    return digest[:32].ljust(32, b"\x00")


def _parse_balance(result: dict) -> int:
    value = result.get("result", 0)
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def _build_signed_transfer(cfg, sender_kp, recipient_hex: str, *, nonce: int, value: int) -> tuple[str, str]:
    sender_bytes = _address_bytes(sender_kp.address)
    recipient_bytes = bytes.fromhex(recipient_hex[2:])
    unsigned = UnsignedTx(
        chain_id=cfg.chain_id,
        nonce=nonce,
        gas_price=1,
        gas_limit=21_000,
        sender=sender_bytes,
        kind=TxKind.TRANSFER,
        payload=TxTransfer(to=recipient_bytes, amount=value, data=b""),
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
    return raw_hex, tx_hash


def test_mempool_mine_includes_raw_tx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")

    client, cfg, _ = new_test_client()
    sender_kp = keygen_sig("dilithium3")
    receiver_kp = keygen_sig("dilithium3")

    receiver_hex = "0x" + _address_bytes(receiver_kp.address).hex()

    mine_fund = rpc_call(
        client,
        "miner.mine",
        {"count": 1, "address": sender_kp.address, "allow_offline_mining": True},
    )["result"]
    assert mine_fund["mined"] == 1

    raw_hex, tx_hash = _build_signed_transfer(
        cfg, sender_kp, receiver_hex, nonce=0, value=17
    )
    send_resp = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert send_resp["result"] == tx_hash

    pending = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash in pending

    mine_res = rpc_call(
        client,
        "miner.mine",
        {"count": 1, "address": sender_kp.address, "allow_offline_mining": True},
    )["result"]
    assert mine_res["mined"] == 1

    pending_after = rpc_call(client, "mempool.getPending")["result"]
    assert tx_hash not in pending_after

    receiver_balance = _parse_balance(rpc_call(client, "state.getBalance", [receiver_hex]))
    assert receiver_balance >= 17


def test_mempool_rejects_raw_hash_mismatch_with_explain() -> None:
    client, cfg, _ = new_test_client()
    sender_kp = keygen_sig("dilithium3")
    receiver_kp = keygen_sig("dilithium3")
    receiver_hex = "0x" + _address_bytes(receiver_kp.address).hex()

    raw_hex, _tx_hash = _build_signed_transfer(
        cfg, sender_kp, receiver_hex, nonce=0, value=1_000_000
    )
    raw = bytes.fromhex(raw_hex[2:])
    _decoded, obj = tx_methods._decode_tx(raw)

    tampered = dict(obj)
    tampered_hash = "0x" + ("00" * 32)
    tampered["hash"] = tampered_hash

    ctx = deps.get_ctx()
    with pytest.raises(Exception):
        ctx.mempool.submit(tx=tampered, raw=raw, tx_hash_hex=tampered_hash)

    explain = rpc_call(client, "mempool.explain", [tampered_hash])["result"]
    assert explain.get("status") == "rejected"
    assert explain.get("reason") is not None
    assert explain.get("reason") == "hash_mismatch"
