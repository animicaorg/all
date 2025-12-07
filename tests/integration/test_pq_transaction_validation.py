"""
Integration test: PQ signature validation on transaction critical path.

Demonstrates:
1. PQ keypair generation
2. Signing transaction payload with PQ signature
3. Verification success with valid signature
4. Verification failure with corrupted signature
"""

from __future__ import annotations

import pytest

from pq.py import keygen, sign, verify
from pq.py.registry import ALG_ID


@pytest.fixture
def dilithium3_keypair():
    """Generate a Dilithium3 keypair for testing."""
    try:
        kp = keygen.keygen_sig("dilithium3")
        return {
            "alg": "dilithium3",
            "alg_id": kp.alg_id,
            "pk": kp.public_key,
            "sk": kp.secret_key,
        }
    except Exception as e:
        pytest.skip(f"Dilithium3 backend not available: {e}")


@pytest.fixture
def sphincs_keypair():
    """Generate a SPHINCS+ keypair for testing."""
    try:
        kp = keygen.keygen_sig("sphincs_shake_128s")
        return {
            "alg": "sphincs_shake_128s",
            "alg_id": kp.alg_id,
            "pk": kp.public_key,
            "sk": kp.secret_key,
        }
    except Exception as e:
        pytest.skip(f"SPHINCS+ backend not available: {e}")


def test_dilithium3_sign_and_verify(dilithium3_keypair):
    """Test Dilithium3 signing and verification of transaction payload."""
    kp = dilithium3_keypair

    # Simulate transaction payload
    tx_payload = b"from:alice|to:bob|amount:1000|nonce:42"
    domain = "tx/sign"
    chain_id = 1337

    # Sign the transaction
    signature = sign.sign_detached(
        tx_payload, alg=kp["alg"], sk=kp["sk"], domain=domain, chain_id=chain_id
    )

    assert signature is not None
    assert signature.alg_name == "dilithium3"
    assert signature.domain == domain.encode("utf-8")
    assert len(signature.signature_bytes) > 0

    # Verify the signature
    is_valid = verify.verify_detached(
        tx_payload, signature, kp["pk"], domain=domain, chain_id=chain_id
    )

    assert is_valid, "Valid signature should verify successfully"


def test_dilithium3_verify_fails_on_corrupted_signature(dilithium3_keypair):
    """Test that verification fails with corrupted signature."""
    kp = dilithium3_keypair

    tx_payload = b"from:alice|to:bob|amount:1000|nonce:42"
    domain = "tx/sign"
    chain_id = 1337

    # Sign the transaction
    signature = sign.sign_detached(
        tx_payload, alg=kp["alg"], sk=kp["sk"], domain=domain, chain_id=chain_id
    )

    # Corrupt the signature by flipping a bit
    corrupted_sig_bytes = bytearray(signature.signature_bytes)
    corrupted_sig_bytes[10] ^= 0x01  # Flip one bit
    corrupted_sig_bytes = bytes(corrupted_sig_bytes)

    # Create corrupted signature object
    from pq.py.sign import Signature

    corrupted_signature = Signature(
        alg_id=signature.alg_id,
        alg_name=signature.alg_name,
        signature_bytes=corrupted_sig_bytes,
        domain=signature.domain,
        prehash=signature.prehash,
    )

    # Verify should fail
    is_valid = verify.verify_detached(
        tx_payload, corrupted_signature, kp["pk"], domain=domain, chain_id=chain_id
    )

    assert not is_valid, "Corrupted signature should fail verification"


def test_dilithium3_verify_fails_on_wrong_message(dilithium3_keypair):
    """Test that verification fails with different message."""
    kp = dilithium3_keypair

    tx_payload = b"from:alice|to:bob|amount:1000|nonce:42"
    wrong_payload = b"from:alice|to:bob|amount:2000|nonce:42"  # Different amount
    domain = "tx/sign"
    chain_id = 1337

    # Sign the original transaction
    signature = sign.sign_detached(
        tx_payload, alg=kp["alg"], sk=kp["sk"], domain=domain, chain_id=chain_id
    )

    # Verify with wrong payload should fail
    is_valid = verify.verify_detached(
        wrong_payload, signature, kp["pk"], domain=domain, chain_id=chain_id
    )

    assert not is_valid, "Signature should not verify for different message"


def test_sphincs_sign_and_verify(sphincs_keypair):
    """Test SPHINCS+ signing and verification."""
    kp = sphincs_keypair

    tx_payload = b"from:alice|to:charlie|amount:500|nonce:1"
    domain = "tx/sign"
    chain_id = 1337

    # Sign the transaction
    signature = sign.sign_detached(
        tx_payload, alg=kp["alg"], sk=kp["sk"], domain=domain, chain_id=chain_id
    )

    assert signature is not None
    assert signature.alg_name == "sphincs_shake_128s"
    assert len(signature.signature_bytes) > 0

    # Verify the signature
    is_valid = verify.verify_detached(
        tx_payload, signature, kp["pk"], domain=domain, chain_id=chain_id
    )

    assert is_valid, "Valid SPHINCS+ signature should verify successfully"


def test_domain_separation(dilithium3_keypair):
    """Test that domain separation prevents signature reuse across contexts."""
    kp = dilithium3_keypair

    payload = b"important_data"
    domain_tx = "tx/sign"
    domain_header = "header/proposer"
    chain_id = 1337

    # Sign with transaction domain
    sig_tx = sign.sign_detached(
        payload, alg=kp["alg"], sk=kp["sk"], domain=domain_tx, chain_id=chain_id
    )

    # Attempt to verify with wrong domain should fail
    is_valid = verify.verify_detached(
        payload,
        sig_tx,
        kp["pk"],
        domain=domain_header,  # Wrong domain
        chain_id=chain_id,
        strict_domain=True,
    )

    assert not is_valid, "Signature should not verify with different domain"


def test_chain_id_separation(dilithium3_keypair):
    """Test that chain_id prevents signature replay across chains."""
    kp = dilithium3_keypair

    payload = b"cross_chain_tx"
    domain = "tx/sign"
    chain_id_mainnet = 1
    chain_id_testnet = 1337

    # Sign with mainnet chain_id
    sig_mainnet = sign.sign_detached(
        payload,
        alg=kp["alg"],
        sk=kp["sk"],
        domain=domain,
        chain_id=chain_id_mainnet,
    )

    # Attempt to verify with testnet chain_id should fail
    is_valid = verify.verify_detached(
        payload, sig_mainnet, kp["pk"], domain=domain, chain_id=chain_id_testnet
    )

    assert not is_valid, "Signature should not verify with different chain_id"


def test_pq_account_transaction_flow(dilithium3_keypair):
    """
    Simulate a full PQ account transaction flow:
    1. Generate PQ keypair
    2. Derive address from public key
    3. Create transaction
    4. Sign transaction
    5. Verify signature
    """
    kp = dilithium3_keypair

    # Derive address from public key (using hash of pubkey as per spec)
    from pq.py.utils.hash import sha3_256

    pubkey_hash = sha3_256(kp["pk"])
    # Address payload = alg_id_byte || sha3_256(pubkey)
    address_payload = bytes([kp["alg_id"]]) + pubkey_hash

    # Transaction fields
    tx_fields = {
        "from": address_payload,
        "to": b"\x01" + b"\x00" * 32,  # Some destination
        "value": 1000,
        "nonce": 42,
        "gas": 21000,
        "data": b"",
    }

    # Create canonical transaction bytes (simplified)
    import json

    tx_canonical = json.dumps(
        {
            "from": tx_fields["from"].hex(),
            "to": tx_fields["to"].hex(),
            "value": tx_fields["value"],
            "nonce": tx_fields["nonce"],
            "gas": tx_fields["gas"],
            "data": tx_fields["data"].hex(),
        },
        sort_keys=True,
    ).encode("utf-8")

    # Sign transaction
    domain = "tx/sign"
    chain_id = 1
    signature = sign.sign_detached(
        tx_canonical, alg=kp["alg"], sk=kp["sk"], domain=domain, chain_id=chain_id
    )

    # Node would verify signature on transaction admission
    is_valid = verify.verify_detached(
        tx_canonical, signature, kp["pk"], domain=domain, chain_id=chain_id
    )

    assert is_valid, "Transaction signature should verify successfully"

    # Node would also verify address matches public key
    recovered_hash = sha3_256(kp["pk"])
    assert (
        address_payload[1:] == recovered_hash
    ), "Address should match public key hash"
    assert address_payload[0] == kp["alg_id"], "Address should encode correct alg_id"


if __name__ == "__main__":
    # Run with: python -m pytest tests/integration/test_pq_transaction_validation.py -v
    pytest.main([__file__, "-v"])
