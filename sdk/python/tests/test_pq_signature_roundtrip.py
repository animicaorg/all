"""
Test PQ signature round-trip between SDK and node verification.

This test ensures that transactions signed by the SDK can be verified
by the node's verification logic, preventing the -32012 error.
"""

import pytest


def _seed(n: int = 32) -> bytes:
    """Deterministic test seed."""
    return bytes(range(n))


def test_pq_signer_sign_tx_with_chain_id():
    """Test that PQSigner.sign_tx properly signs with domain and chain_id."""
    from omni_sdk.wallet.signer import PQSigner
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import sign_bytes
    
    # Create a deterministic signer
    try:
        signer = PQSigner.from_seed("dilithium3", seed=_seed())
    except RuntimeError as exc:
        pytest.skip(f"PQ keygen not available: {exc}")
    
    # Build a real transaction
    chain_id = 1
    tx = transfer(
        from_addr=signer.address or "anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=chain_id,
    )
    
    # Get message to sign (CBOR-encoded body)
    msg = sign_bytes(tx)
    
    # Sign with sign_tx method
    signature = signer.sign_tx(msg, chain_id)
    
    # Check signature is valid bytes
    assert isinstance(signature, bytes)
    assert len(signature) > 0
    
    # Dilithium3 signatures should be around 2420 bytes
    assert len(signature) > 2000


def test_sign_tx_handles_legacy_sign_detached(monkeypatch):
    """Ensure sign_tx preserves domain separation if pq.sign lacks chain_id support."""

    from omni_sdk.wallet.signer import PQSigner
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import sign_bytes

    try:
        import pq.py.sign as pq_sign
    except ImportError:
        pytest.skip("PQ signing not available")

    try:
        signer = PQSigner.from_seed("dilithium3", seed=_seed())
    except RuntimeError as exc:
        pytest.skip(f"PQ keygen not available: {exc}")
    chain_id = 1

    tx = transfer(
        from_addr=signer.address or "anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=chain_id,
    )

    msg = sign_bytes(tx)

    # Simulate a legacy pq.sign implementation that does not accept chain_id
    def legacy_sign_detached(msg_arg, alg_name, sk, domain=None):  # type: ignore[no-untyped-def]
        raise TypeError("legacy sign_detached without chain_id")

    captured = {}

    def capturing_backend_sign(alg_name, sk, prehash):  # type: ignore[no-untyped-def]
        captured["prehash"] = prehash
        return original_backend_sign(alg_name, sk, prehash)

    original_backend_sign = pq_sign._backend_sign
    monkeypatch.setattr(pq_sign, "sign_detached", legacy_sign_detached)
    monkeypatch.setattr(pq_sign, "_backend_sign", capturing_backend_sign)

    signature = signer.sign_tx(msg, chain_id)

    assert isinstance(signature, bytes) and len(signature) > 0

    expected_prehash = pq_sign.build_sign_bytes(
        msg,
        domain="tx",
        chain_id=chain_id,
        alg_id=signer.alg_id,
    )
    assert captured.get("prehash") == expected_prehash


def test_sdk_sign_bytes_returns_cbor_body():
    """Test that sign_bytes returns raw CBOR body without domain wrapping."""
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import sign_bytes, canonical_body_dict
    from omni_sdk.utils.cbor import dumps as cbor_dumps
    
    # Build a sample transaction
    tx = transfer(
        from_addr="anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=1,
    )
    
    # Get sign_bytes
    sign_bytes_data = sign_bytes(tx)
    
    # Should be CBOR-encoded
    assert isinstance(sign_bytes_data, bytes)
    assert len(sign_bytes_data) > 0
    
    # Should match direct CBOR encoding of body
    expected = cbor_dumps(canonical_body_dict(tx))
    assert sign_bytes_data == expected


def test_node_verification_matches_sdk_signature():
    """Test that node verification accepts SDK-signed transactions."""
    from omni_sdk.wallet.signer import PQSigner
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import sign_bytes
    
    # Import node verification components (if available)
    try:
        from pq.py.sign import Signature
        from pq.py.verify import verify_detached
    except ImportError:
        pytest.skip("PQ verification not available")
    
    # Create signer
    signer = PQSigner.from_seed("dilithium3", seed=_seed())
    
    # Build transaction
    chain_id = 1
    tx = transfer(
        from_addr=signer.address or "anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=chain_id,
    )
    
    # Sign using SDK
    msg = sign_bytes(tx)
    sig_bytes = signer.sign_tx(msg, chain_id)
    
    # Create signature envelope (as node does)
    sig_env = Signature(
        alg_id=signer.alg_id,
        alg_name=signer.alg_name,
        domain="tx",
        prehash="sha3-512",
        sig=sig_bytes,
    )
    
    # Verify using node's verification path
    ok = verify_detached(msg, sig_env, signer.public_key, chain_id=chain_id)
    
    assert ok is True, "Node verification should accept SDK signature"


def test_node_verification_rejects_flipped_signature():
    """Test that node verification rejects tampered signatures."""
    from omni_sdk.wallet.signer import PQSigner
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import sign_bytes
    
    try:
        from pq.py.sign import Signature
        from pq.py.verify import verify_detached
    except ImportError:
        pytest.skip("PQ verification not available")
    
    # Create signer
    signer = PQSigner.from_seed("dilithium3", seed=_seed())
    
    # Build transaction
    chain_id = 1
    tx = transfer(
        from_addr=signer.address or "anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=chain_id,
    )
    
    # Sign using SDK
    msg = sign_bytes(tx)
    sig_bytes = signer.sign_tx(msg, chain_id)
    
    # Tamper with signature (flip a byte in the middle)
    tampered_sig = bytearray(sig_bytes)
    # Use middle of signature to avoid issues with short signatures
    tamper_index = len(tampered_sig) // 2
    tampered_sig[tamper_index] ^= 0xFF
    tampered_sig = bytes(tampered_sig)
    
    # Create signature envelope with tampered sig
    sig_env = Signature(
        alg_id=signer.alg_id,
        alg_name=signer.alg_name,
        domain="tx",
        prehash="sha3-512",
        sig=tampered_sig,
    )
    
    # Verify should fail
    ok = verify_detached(msg, sig_env, signer.public_key, chain_id=chain_id)
    
    assert ok is False, "Node verification should reject tampered signature"


def test_node_verification_rejects_wrong_chain_id():
    """Test that signatures for different chain IDs are rejected."""
    from omni_sdk.wallet.signer import PQSigner
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import sign_bytes
    
    try:
        from pq.py.sign import Signature
        from pq.py.verify import verify_detached
    except ImportError:
        pytest.skip("PQ verification not available")
    
    # Create signer
    signer = PQSigner.from_seed("dilithium3", seed=_seed())
    
    # Build transaction for chain_id=1
    chain_id = 1
    tx = transfer(
        from_addr=signer.address or "anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=chain_id,
    )
    
    # Sign using SDK with chain_id=1
    msg = sign_bytes(tx)
    sig_bytes = signer.sign_tx(msg, chain_id)
    
    # Create signature envelope
    sig_env = Signature(
        alg_id=signer.alg_id,
        alg_name=signer.alg_name,
        domain="tx",
        prehash="sha3-512",
        sig=sig_bytes,
    )
    
    # Try to verify with wrong chain_id=2
    ok = verify_detached(msg, sig_env, signer.public_key, chain_id=2)
    
    assert ok is False, "Node verification should reject signature with wrong chain_id"


def test_packed_signed_envelope_has_required_fields():
    """Test that pack_signed creates envelope with all required fields."""
    from omni_sdk.wallet.signer import PQSigner
    from omni_sdk.tx.build import transfer
    from omni_sdk.tx.encode import sign_bytes, pack_signed, unpack_signed

    # Create signer
    try:
        signer = PQSigner.from_seed("dilithium3", seed=_seed())
    except RuntimeError as exc:
        pytest.skip(f"PQ keygen not available: {exc}")

    # Build transaction
    chain_id = 1
    tx = transfer(
        from_addr=signer.address or "anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=chain_id,
    )
    
    # Sign
    msg = sign_bytes(tx)
    sig_bytes = signer.sign_tx(msg, chain_id)
    
    # Pack into signed envelope
    raw_tx = pack_signed(
        tx,
        signature=sig_bytes,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    
    # Unpack and verify structure
    envelope = unpack_signed(raw_tx)
    
    assert "body" in envelope
    assert "sig" in envelope
    
    # Check body has chain_id
    body = envelope["body"]
    assert "chainId" in body
    assert body["chainId"] == chain_id
    
    # Check sig envelope
    sig_env = envelope["sig"]
    assert "algId" in sig_env
    assert "pubkey" in sig_env
    assert "sig" in sig_env
    assert sig_env["algId"] == signer.alg_id
    assert sig_env["pubkey"] == signer.public_key
    assert sig_env["sig"] == sig_bytes
