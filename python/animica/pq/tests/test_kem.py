"""Tests for ML-KEM-768 (Kyber768) key encapsulation mechanism."""

import os
import pytest


def test_kem_availability():
    """Test that KEM is available in pure mode."""
    from animica.pq import is_available, get_mode
    
    # Should be available by default
    assert is_available()
    assert get_mode() == "pure"


def test_kem_keygen_random():
    """Test KEM keypair generation with random seed."""
    from animica.pq import kem_keygen
    
    ek1, dk1 = kem_keygen()
    ek2, dk2 = kem_keygen()
    
    # Keys should be correct length
    assert len(ek1) == 1184  # ML-KEM-768 public key
    assert len(dk1) == 2400  # ML-KEM-768 secret key
    
    # Different calls should produce different keys
    assert ek1 != ek2
    assert dk1 != dk2


def test_kem_keygen_deterministic():
    """Test KEM keypair generation with fixed seed (deterministic)."""
    from animica.pq import kem_keygen
    
    seed = b"0" * 64
    ek1, dk1 = kem_keygen(seed)
    ek2, dk2 = kem_keygen(seed)
    
    # Same seed should produce same keys
    assert ek1 == ek2
    assert dk1 == dk2
    
    # Keys should be correct length
    assert len(ek1) == 1184
    assert len(dk1) == 2400


def test_kem_encaps_decaps_roundtrip():
    """Test KEM encapsulation/decapsulation roundtrip."""
    from animica.pq import kem_keygen, kem_encaps, kem_decaps
    
    # Generate keypair
    ek, dk = kem_keygen()
    
    # Encapsulate
    ss1, ct = kem_encaps(ek)
    
    # Check sizes
    assert len(ss1) == 32    # shared secret
    assert len(ct) == 1088   # ciphertext
    
    # Decapsulate
    ss2 = kem_decaps(dk, ct)
    
    # Shared secrets should match
    assert ss1 == ss2


def test_kem_encaps_deterministic():
    """Test KEM encapsulation is deterministic with fixed seed."""
    from animica.pq import kem_keygen, kem_encaps
    
    # Generate keypair with fixed seed
    seed = b"test" * 16
    ek, dk = kem_keygen(seed)
    
    # Encapsulate with fixed seed
    encaps_seed = b"encaps_test" + b"\x00" * 21
    ss1, ct1 = kem_encaps(ek, encaps_seed)
    ss2, ct2 = kem_encaps(ek, encaps_seed)
    
    # Should be deterministic
    assert ss1 == ss2
    assert ct1 == ct2


def test_kem_different_ciphertexts():
    """Test that different encapsulations produce different ciphertexts."""
    from animica.pq import kem_keygen, kem_encaps
    
    ek, dk = kem_keygen()
    
    # Multiple encapsulations should produce different ciphertexts
    ss1, ct1 = kem_encaps(ek)
    ss2, ct2 = kem_encaps(ek)
    
    assert ct1 != ct2
    # Note: shared secrets will also be different for different ciphertexts


def test_kem_disabled_mode():
    """Test KEM behavior when PQ is disabled."""
    from animica.pq import kem_keygen
    
    # Temporarily disable PQ
    old_mode = os.environ.get("ANIMICA_PQ_MODE")
    try:
        os.environ["ANIMICA_PQ_MODE"] = "disabled"
        
        # Should raise RuntimeError
        with pytest.raises(RuntimeError, match="disabled"):
            kem_keygen()
    finally:
        if old_mode is None:
            os.environ.pop("ANIMICA_PQ_MODE", None)
        else:
            os.environ["ANIMICA_PQ_MODE"] = old_mode


def test_kem_invalid_key_sizes():
    """Test KEM with invalid key sizes."""
    from animica.pq import kem_keygen, kem_encaps, kem_decaps
    
    ek, dk = kem_keygen()
    ss, ct = kem_encaps(ek)
    
    # Invalid encapsulation key
    with pytest.raises(ValueError):
        kem_encaps(ek[:100])
    
    # Invalid decapsulation key
    with pytest.raises(ValueError):
        kem_decaps(dk[:100], ct)
    
    # Invalid ciphertext
    with pytest.raises(ValueError):
        kem_decaps(dk, ct[:100])


def test_kem_seed_validation():
    """Test KEM seed validation."""
    from animica.pq import kem_keygen, kem_encaps
    
    # Invalid keygen seed (wrong size)
    with pytest.raises(ValueError, match="64 bytes"):
        kem_keygen(b"short")
    
    # Invalid encaps seed (wrong size)
    ek, dk = kem_keygen()
    with pytest.raises(ValueError, match="32 bytes"):
        kem_encaps(ek, b"short")


def test_kem_multiple_roundtrips():
    """Test multiple KEM roundtrips with same keypair."""
    from animica.pq import kem_keygen, kem_encaps, kem_decaps
    
    ek, dk = kem_keygen()
    
    # Multiple encapsulations should all decapsulate correctly
    for _ in range(5):
        ss_enc, ct = kem_encaps(ek)
        ss_dec = kem_decaps(dk, ct)
        assert ss_enc == ss_dec
