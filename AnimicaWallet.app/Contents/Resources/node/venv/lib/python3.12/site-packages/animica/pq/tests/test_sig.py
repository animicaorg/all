"""Tests for ML-DSA-65 (Dilithium3) digital signatures."""

import os
import pytest


def test_sig_availability():
    """Test that signatures are available in pure mode."""
    from animica.pq import is_available, get_mode
    
    assert is_available()
    assert get_mode() == "pure"


def test_sig_keygen_random():
    """Test signature keypair generation with random seed."""
    from animica.pq import sig_keygen
    
    pk1, sk1 = sig_keygen()
    pk2, sk2 = sig_keygen()
    
    # Keys should be correct length
    assert len(pk1) == 1952  # ML-DSA-65 public key
    assert len(sk1) == 4000  # ML-DSA-65 secret key
    
    # Different calls should produce different keys
    assert pk1 != pk2
    assert sk1 != sk2


def test_sig_keygen_deterministic():
    """Test signature keypair generation with fixed seed."""
    from animica.pq import sig_keygen
    
    seed = b"0" * 32
    pk1, sk1 = sig_keygen(seed)
    pk2, sk2 = sig_keygen(seed)
    
    # Same seed should produce same keys
    assert pk1 == pk2
    assert sk1 == sk2
    
    # Keys should be correct length
    assert len(pk1) == 1952
    assert len(sk1) == 4000


def test_sig_sign_verify_roundtrip():
    """Test signature sign/verify roundtrip."""
    from animica.pq import sig_keygen, sig_sign, sig_verify
    
    # Generate keypair
    pk, sk = sig_keygen()
    
    # Sign message
    message = b"Hello, Animica!"
    sig = sig_sign(sk, message)
    
    # Check signature size
    assert len(sig) == 3293  # ML-DSA-65 signature
    
    # Verify signature
    assert sig_verify(pk, message, sig)


def test_sig_verify_wrong_message():
    """Test that signature fails on wrong message."""
    from animica.pq import sig_keygen, sig_sign, sig_verify
    
    pk, sk = sig_keygen()
    message1 = b"Original message"
    message2 = b"Different message"
    
    sig = sig_sign(sk, message1)
    
    # Should verify with correct message
    assert sig_verify(pk, message1, sig)
    
    # Should fail with wrong message
    assert not sig_verify(pk, message2, sig)


def test_sig_verify_tampered_signature():
    """Test that tampered signature fails verification."""
    from animica.pq import sig_keygen, sig_sign, sig_verify
    
    pk, sk = sig_keygen()
    message = b"Test message"
    sig = sig_sign(sk, message)
    
    # Tamper with signature
    tampered_sig = bytearray(sig)
    tampered_sig[0] ^= 0x01
    tampered_sig = bytes(tampered_sig)
    
    # Should fail verification
    assert not sig_verify(pk, message, tampered_sig)


def test_sig_deterministic_signing():
    """Test that signing is deterministic (hedged by default)."""
    from animica.pq import sig_keygen, sig_sign
    
    seed = b"0" * 32
    pk, sk = sig_keygen(seed)
    message = b"Test message"
    
    # Multiple signatures with same key and message should be identical
    sig1 = sig_sign(sk, message)
    sig2 = sig_sign(sk, message)
    
    assert sig1 == sig2


def test_sig_different_messages():
    """Test that different messages produce different signatures."""
    from animica.pq import sig_keygen, sig_sign
    
    pk, sk = sig_keygen()
    
    sig1 = sig_sign(sk, b"Message 1")
    sig2 = sig_sign(sk, b"Message 2")
    
    assert sig1 != sig2


def test_sig_disabled_mode():
    """Test signature behavior when PQ is disabled."""
    from animica.pq import sig_keygen
    
    old_mode = os.environ.get("ANIMICA_PQ_MODE")
    try:
        os.environ["ANIMICA_PQ_MODE"] = "disabled"
        
        with pytest.raises(RuntimeError, match="disabled"):
            sig_keygen()
    finally:
        if old_mode is None:
            os.environ.pop("ANIMICA_PQ_MODE", None)
        else:
            os.environ["ANIMICA_PQ_MODE"] = old_mode


def test_sig_invalid_key_sizes():
    """Test signatures with invalid key sizes."""
    from animica.pq import sig_keygen, sig_sign, sig_verify
    
    pk, sk = sig_keygen()
    message = b"Test"
    
    # Invalid secret key
    with pytest.raises(ValueError):
        sig_sign(sk[:100], message)
    
    # Invalid public key for verification
    sig = sig_sign(sk, message)
    with pytest.raises(ValueError):
        sig_verify(pk[:100], message, sig)


def test_sig_invalid_signature_length():
    """Test verification with invalid signature length."""
    from animica.pq import sig_keygen, sig_verify
    
    pk, sk = sig_keygen()
    message = b"Test"
    
    # Wrong signature length should return False (not raise)
    assert not sig_verify(pk, message, b"short_sig")


def test_sig_seed_validation():
    """Test signature seed validation."""
    from animica.pq import sig_keygen
    
    # Invalid seed (wrong size)
    with pytest.raises(ValueError, match="32 bytes"):
        sig_keygen(b"short")


def test_sig_empty_message():
    """Test signing empty message."""
    from animica.pq import sig_keygen, sig_sign, sig_verify
    
    pk, sk = sig_keygen()
    message = b""
    
    sig = sig_sign(sk, message)
    assert sig_verify(pk, message, sig)


def test_sig_large_message():
    """Test signing large message."""
    from animica.pq import sig_keygen, sig_sign, sig_verify
    
    pk, sk = sig_keygen()
    message = b"A" * 10000  # 10KB message
    
    sig = sig_sign(sk, message)
    assert sig_verify(pk, message, sig)


def test_sig_multiple_signatures():
    """Test multiple signatures with same keypair."""
    from animica.pq import sig_keygen, sig_sign, sig_verify
    
    pk, sk = sig_keygen()
    
    # Sign multiple different messages
    messages = [b"msg1", b"msg2", b"msg3", b"msg4", b"msg5"]
    signatures = []
    
    for msg in messages:
        sig = sig_sign(sk, msg)
        signatures.append(sig)
        assert sig_verify(pk, msg, sig)
    
    # All signatures should be different
    assert len(set(signatures)) == len(signatures)
    
    # Cross-verification should fail
    for i, msg in enumerate(messages):
        for j, sig in enumerate(signatures):
            if i == j:
                assert sig_verify(pk, msg, sig)
            else:
                assert not sig_verify(pk, msg, sig)
