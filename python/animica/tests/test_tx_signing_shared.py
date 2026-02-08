from __future__ import annotations

from pq.py.keygen import keygen_sig

from animica.tx.signing import ChainContext, pq_sign_tx, pq_verify_tx


def _sample_tx() -> dict:
    return {
        "to": b"\x22" * 32,
        "from": b"\x11" * 32,
        "value": 100,
        "gasLimit": 21000,
        "maxFee": 1_000_000_000,
        "data": b"",
        "chainId": 1,
        "validAfter": 1,
        "validUntil": 100,
        "salt": b"\x00" * 16,
    }


def test_pq_sign_and_verify_roundtrip_shared_module() -> None:
    kp = keygen_sig("sphincs_shake_128s")
    tx_body = _sample_tx()
    ctx = ChainContext(chain_id=1, genesis_hash=b"\x33" * 32, network="devnet", domain="tx", prehash="sha3-512")

    sig = pq_sign_tx(tx_body, kp.secret_key, kp.public_key, kp.alg_id, ctx)
    vr = pq_verify_tx(tx_body, sig, kp.public_key, ctx)

    assert vr.ok is True
    assert vr.reason is None


def test_pq_verify_rejects_from_address_pubkey_mismatch() -> None:
    signer_a = keygen_sig("sphincs_shake_128s")
    signer_b = keygen_sig("sphincs_shake_128s")
    tx_body = _sample_tx()
    ctx = ChainContext(chain_id=1, genesis_hash=b"\x33" * 32, network="devnet", domain="tx", prehash="sha3-512")

    sig = pq_sign_tx(tx_body, signer_a.secret_key, signer_a.public_key, signer_a.alg_id, ctx)
    vr = pq_verify_tx(tx_body, sig, signer_a.public_key, ctx, from_addr=signer_b.address)

    assert vr.ok is False
    assert vr.reason == "from address does not match signature public key"
