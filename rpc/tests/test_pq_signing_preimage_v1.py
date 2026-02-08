import pytest

from animica.tx.signing import tx_signing_preimage


def _sample_body():
    return {
        "body": {
            "v": 2,
            "chainId": 1,
            "from": "anim1abc",
            "to": "anim1def",
            "nonce": 7,
            "value": 9,
            "gasLimit": 21000,
            "maxFee": 1000,
            "data": b"",
        },
        "sig": {"alg": 4097, "pubkey": b"pk", "sig": b"sg"},
    }


def test_preimage_deterministic_and_excludes_signature():
    tx = _sample_body()
    a = tx_signing_preimage(tx, chain_id=1, genesis=b"\x11" * 32, network="devnet")
    b = tx_signing_preimage(tx, chain_id=1, genesis=b"\x11" * 32, network="devnet")
    assert a == b

    tx["sig"]["sig"] = b"tampered"
    c = tx_signing_preimage(tx, chain_id=1, genesis=b"\x11" * 32, network="devnet")
    assert c == a


def test_preimage_changes_with_domain_fields():
    tx = _sample_body()
    base = tx_signing_preimage(tx, chain_id=1, genesis=b"\x11" * 32, network="devnet")
    assert tx_signing_preimage(tx, chain_id=2, genesis=b"\x11" * 32, network="devnet") != base
    assert tx_signing_preimage(tx, chain_id=1, genesis=b"\x22" * 32, network="devnet") != base
    assert tx_signing_preimage(tx, chain_id=1, genesis=b"\x11" * 32, network="mainnet") != base
