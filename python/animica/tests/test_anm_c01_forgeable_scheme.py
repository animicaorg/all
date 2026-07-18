"""ANM-C01 / ANM-C02 regression.

The legacy PQ signature schemes 0x1001 ("dilithium3") and 0x1002
("sphincs_shake_128s") are FORGEABLE stubs: verify() recomputes a public
commitment shake_256(pubkey[:32] || msg) with no secret key, so anyone holding a
public key can forge a valid signature for any message. Combined with the state
DB keying accounts by sha3_256(pubkey) (alg_id stripped), a forged 0x1001 tx
drains the same balance as the victim's real ml_dsa_65 (0x1003) account.

The fix is an always-on production allowlist in tx_verify_signature: only 0x1003
may authorize a transaction, enforced at every mempool/relay/mining call BEFORE
the forgeable stub verifier is ever reached.
"""
import hashlib

import pytest

from animica.tx.signing import (
    ACCEPTED_TX_SIG_ALG_IDS,
    ChainContext,
    _tx_scheme_accepted,
    tx_verify_signature,
)
from pq.py.sign import Signature

ML_DSA_65 = 0x1003
DILITHIUM3_STUB = 0x1001
SPHINCS_STUB = 0x1002


def _ctx(chain_id: int = 1) -> ChainContext:
    return ChainContext(
        chain_id=chain_id,
        genesis_hash=b"",
        network="mainnet",
        fork_id=0,
        domain="tx",
        prehash="sha3-512",
    )


def _minimal_tx() -> dict:
    return {
        "from": "anim1test",
        "to": "anim1dest",
        "amount": 1000,
        "nonce": 0,
        "chainId": 1,
        "gasLimit": 21000,
        "gasPrice": 1,
    }


def test_allowlist_is_only_ml_dsa_65():
    assert ACCEPTED_TX_SIG_ALG_IDS == frozenset({0x1003})
    assert _tx_scheme_accepted(ML_DSA_65)
    assert not _tx_scheme_accepted(DILITHIUM3_STUB)
    assert not _tx_scheme_accepted(SPHINCS_STUB)
    assert not _tx_scheme_accepted(4097)  # 0x1001 decimal alias
    assert not _tx_scheme_accepted(4098)  # 0x1002 decimal alias


def test_dilithium3_stub_is_actually_forgeable():
    """Documents the vulnerability: the 0x1001 verifier is pubkey-only."""
    from animica._vendor.dilithium_py.dilithium3 import Dilithium3

    pk = (bytes(range(256)) * 8)[:1952]  # 1952-byte pubkey (DILITHIUM_PUBLICKEYBYTES)
    msg = b"drain the victim"
    # Forge WITHOUT any secret key, exactly as the stub verifies:
    commitment = hashlib.shake_256(pk[:32] + msg).digest(32)
    forged = commitment + hashlib.shake_256(b"pad" + commitment).digest(3293 - 32)
    assert len(forged) == 3293
    assert Dilithium3.verify(pk, msg, forged) is True  # <-- forgeable, no sk used


def test_tx_verify_rejects_forged_0x1001():
    pk = (bytes(range(256)) * 8)[:1952]
    sig = Signature(
        alg_id=DILITHIUM3_STUB, sig=b"\x00" * 3293,
        alg_name="dilithium3", domain="tx", prehash="sha3-512",
    )
    res = tx_verify_signature(_minimal_tx(), sig, pk, _ctx())
    assert res.ok is False
    assert (res.reason or "").startswith("scheme_deprecated"), res.reason


def test_tx_verify_rejects_0x1002():
    pk = b"\x00" * 64
    sig = Signature(
        alg_id=SPHINCS_STUB, sig=b"\x00" * 64,
        alg_name="sphincs_shake_128s", domain="tx", prehash="sha3-512",
    )
    res = tx_verify_signature(_minimal_tx(), sig, pk, _ctx())
    assert res.ok is False
    assert (res.reason or "").startswith("scheme_deprecated"), res.reason
