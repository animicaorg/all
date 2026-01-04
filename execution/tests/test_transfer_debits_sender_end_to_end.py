from __future__ import annotations

from pathlib import Path

import cbor2
import pytest

from pq.py import keygen as pq_keygen
from pq.py import sign as pq_sign
from pq.py.registry import ALG_ID, normalize_alg_name

from core.utils.address import address_to_bytes
from mempool.tx_hash import tx_hash_hex
from rpc.tests import new_test_client, rpc_call


def _build_signed_cli_envelope(
    *,
    chain_id: int,
    sender_kp,
    recipient_addr: str,
    nonce: int,
    value: int,
) -> tuple[bytes, str]:
    alg = normalize_alg_name("dilithium3")
    alg_id = ALG_ID[alg] if isinstance(alg, str) else alg
    sender_bytes = address_to_bytes(sender_kp.address)
    recipient_bytes = address_to_bytes(recipient_addr)

    body = {
        "to": recipient_bytes,
        "from": sender_bytes,
        "value": value,
        "nonce": nonce,
        "gasLimit": 21000,
        "gasPrice": 1,
        "maxFee": 1,
        "validAfter": 0,
        "validUntil": 50,
        "salt": b"e2e-transfer",
        "data": b"",
        "chainId": chain_id,
    }

    body_bytes = cbor2.dumps(body, canonical=True)
    try:
        sig = pq_sign.pq_sign_detached(
            body_bytes,
            alg=alg_id,
            sk=sender_kp.secret_key,
            pk=sender_kp.public_key,
            domain="tx",
            chain_id=chain_id,
            fork_id=None,
            prehash="sha3-512",
        )
    except NotImplementedError as exc:
        pytest.skip(f"PQ signature backend unavailable: {exc}")

    envelope = {
        "sig": {
            "algId": alg_id,
            "pk": sender_kp.public_key,
            "sig": sig.sig,
            "domain": "tx",
            "prehash": "sha3-512",
            "chainId": chain_id,
        },
        "body": body,
    }

    raw = cbor2.dumps(envelope, canonical=True)
    return raw, tx_hash_hex(raw)


def _hex_to_int(value: str) -> int:
    return int(value, 16)


def test_transfer_debits_sender_end_to_end(monkeypatch) -> None:
    genesis_path = Path(__file__).resolve().parents[2] / "core" / "genesis" / "mainnet.json"
    monkeypatch.setenv("ANIMICA_GENESIS_PATH", str(genesis_path))
    client, cfg, _tmpdir = new_test_client()
    alg = normalize_alg_name("dilithium3")
    sender_kp = pq_keygen.keygen_sig(alg)
    recipient_kp = pq_keygen.keygen_sig(alg)

    rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})
    sender_before = _hex_to_int(
        rpc_call(client, "state.getBalance", {"address": sender_kp.address})["result"]
    )
    recipient_before = _hex_to_int(
        rpc_call(client, "state.getBalance", {"address": recipient_kp.address})["result"]
    )

    raw, tx_hash = _build_signed_cli_envelope(
        chain_id=cfg.chain_id,
        sender_kp=sender_kp,
        recipient_addr=recipient_kp.address,
        nonce=0,
        value=7,
    )
    raw_hex = "0x" + raw.hex()

    submit_res = rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex})
    assert submit_res["result"] == tx_hash

    miner_kp = pq_keygen.keygen_sig(alg)
    rpc_call(client, "miner.mine", {"count": 1, "address": miner_kp.address})

    sender_after = _hex_to_int(
        rpc_call(client, "state.getBalance", {"address": sender_kp.address})["result"]
    )
    recipient_after = _hex_to_int(
        rpc_call(client, "state.getBalance", {"address": recipient_kp.address})["result"]
    )

    fee = 21000 * 1
    assert sender_after == sender_before - (7 + fee)
    assert recipient_after == recipient_before + 7


def test_rejected_mempool_tx_does_not_change_balances(monkeypatch) -> None:
    genesis_path = Path(__file__).resolve().parents[2] / "core" / "genesis" / "mainnet.json"
    monkeypatch.setenv("ANIMICA_GENESIS_PATH", str(genesis_path))
    client, cfg, _tmpdir = new_test_client()
    alg = normalize_alg_name("dilithium3")
    sender_kp = pq_keygen.keygen_sig(alg)
    recipient_kp = pq_keygen.keygen_sig(alg)

    rpc_call(client, "miner.mine", {"count": 1, "address": sender_kp.address})
    sender_before = _hex_to_int(
        rpc_call(client, "state.getBalance", {"address": sender_kp.address})["result"]
    )
    recipient_before = _hex_to_int(
        rpc_call(client, "state.getBalance", {"address": recipient_kp.address})["result"]
    )

    raw, _tx_hash = _build_signed_cli_envelope(
        chain_id=cfg.chain_id,
        sender_kp=sender_kp,
        recipient_addr=recipient_kp.address,
        nonce=1,
        value=7,
    )
    raw_hex = "0x" + raw.hex()

    rpc_call(client, "tx.sendRawTransaction", {"rawTx": raw_hex}, expect_error=True)
    miner_kp = pq_keygen.keygen_sig(alg)
    rpc_call(client, "miner.mine", {"count": 1, "address": miner_kp.address})

    sender_after = _hex_to_int(
        rpc_call(client, "state.getBalance", {"address": sender_kp.address})["result"]
    )
    recipient_after = _hex_to_int(
        rpc_call(client, "state.getBalance", {"address": recipient_kp.address})["result"]
    )

    assert sender_after == sender_before
    assert recipient_after == recipient_before
