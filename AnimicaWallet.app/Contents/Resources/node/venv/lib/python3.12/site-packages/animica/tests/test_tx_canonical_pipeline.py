from __future__ import annotations

import pytest

from pq.py.keygen import keygen_sig
from pq.py.sign import Signature

from animica.tx.signing import (
    ChainContext,
    pq_sign_tx,
    tx_canonical_bytes_unsigned,
    tx_sign_hash,
    tx_signing_preimage,
    tx_verify_signature,
)


def _sample_tx() -> dict:
    return {
        "chainId": 1,
        "from": "anim1testsender",
        "to": "anim1testdest",
        "nonce": 9,
        "value": 100,
        "gasLimit": 21000,
        "maxFee": 1000,
        "data": b"",
    }


def test_canonical_unsigned_and_sign_hash_are_stable() -> None:
    tx = _sample_tx()
    ctx = ChainContext(chain_id=1, genesis_hash=b"\x42" * 32, network="devnet")

    c1 = tx_canonical_bytes_unsigned(tx)
    c2 = tx_canonical_bytes_unsigned(dict(tx))
    assert c1 == c2

    p1 = tx_signing_preimage(tx, ctx)
    p2 = tx_signing_preimage(dict(tx), ctx)
    assert p1 == p2

    h1 = tx_sign_hash(tx, ctx)
    h2 = tx_sign_hash(dict(tx), ctx)
    assert h1 == h2


def test_verify_rejects_string_pubkey_and_reports_size_diagnostics() -> None:
    kp = keygen_sig("sphincs_shake_128s")
    tx = _sample_tx()
    ctx = ChainContext(chain_id=1, genesis_hash=b"\x11" * 32, network="devnet", domain="tx", prehash="sha3-512")
    sig = pq_sign_tx(tx, kp.secret_key, kp.public_key, kp.alg_id, ctx)

    with pytest.raises(TypeError, match="pubkey must be raw bytes"):
        tx_verify_signature(tx, sig, kp.public_key.hex(), ctx)  # type: ignore[arg-type]

    wrong_sig = Signature(
        alg_id=sig.alg_id,
        alg_name=sig.alg_name,
        domain=sig.domain,
        prehash=sig.prehash,
        sig=sig.sig[:-1],
    )
    result = tx_verify_signature(tx, wrong_sig, kp.public_key, ctx)
    assert result.ok is False
    assert result.reason is not None and result.reason.startswith("invalid_signature_size")
    assert result.pubkey_len == len(kp.public_key)
    assert result.sig_len == len(sig.sig) - 1

