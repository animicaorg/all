"""Tests for legacy backend compatibility layer."""

import os
import pytest


def test_get_backend_pure_mode():
    """Test get_backend returns pure backend in pure mode."""
    from animica.pq import get_backend
    
    backend, label = get_backend()
    
    assert label == "pure"
    assert hasattr(backend, "keygen")
    assert hasattr(backend, "sign")
    assert hasattr(backend, "verify")


def test_get_backend_disabled_mode():
    """Test get_backend returns disabled backend when PQ is disabled."""
    from animica.pq import get_backend
    
    old_mode = os.environ.get("ANIMICA_PQ_MODE")
    try:
        os.environ["ANIMICA_PQ_MODE"] = "disabled"
        
        backend, label = get_backend()
        assert label == "disabled"
        
        # Operations should raise RuntimeError
        with pytest.raises(RuntimeError, match="disabled"):
            backend.keygen()
        
        with pytest.raises(RuntimeError, match="disabled"):
            backend.sign(b"sk", b"msg")
        
        with pytest.raises(RuntimeError, match="disabled"):
            backend.verify(b"pk", b"msg", b"sig")
    finally:
        if old_mode is None:
            os.environ.pop("ANIMICA_PQ_MODE", None)
        else:
            os.environ["ANIMICA_PQ_MODE"] = old_mode


def test_backend_keygen():
    """Test backend keygen method."""
    from animica.pq import get_backend
    
    backend, _ = get_backend()
    pk, sk = backend.keygen()
    
    # Should return ML-DSA-65 keys
    assert len(pk) == 1952
    assert len(sk) == 4000


def test_backend_sign_verify():
    """Test backend sign/verify methods."""
    from animica.pq import get_backend
    
    backend, _ = get_backend()
    pk, sk = backend.keygen()
    
    message = b"test message"
    sig = backend.sign(sk, message)
    
    assert len(sig) == 3293
    assert backend.verify(pk, message, sig)


def test_backend_roundtrip():
    """Test complete backend roundtrip."""
    from animica.pq import get_backend
    
    backend, label = get_backend()
    assert label == "pure"
    
    # Generate keys
    pk, sk = backend.keygen()
    
    # Sign and verify
    message = b"Hello from legacy backend"
    signature = backend.sign(sk, message)
    
    # Verification should succeed
    assert backend.verify(pk, message, signature)
    
    # Wrong message should fail
    assert not backend.verify(pk, b"different", signature)


def test_legacy_fake_backend_removed():
    """Test that the old fake backend is no longer used."""
    from animica.pq import get_backend
    
    # Even with ANIMICA_UNSAFE_PQ_FAKE, we should get pure backend
    old_fake = os.environ.get("ANIMICA_UNSAFE_PQ_FAKE")
    try:
        os.environ["ANIMICA_UNSAFE_PQ_FAKE"] = "1"
        
        backend, label = get_backend()
        # Should still be pure, not fake
        assert label == "pure"
    finally:
        if old_fake is None:
            os.environ.pop("ANIMICA_UNSAFE_PQ_FAKE", None)
        else:
            os.environ["ANIMICA_UNSAFE_PQ_FAKE"] = old_fake


def test_backend_multiple_operations():
    """Test backend with multiple operations."""
    from animica.pq import get_backend
    
    backend, _ = get_backend()
    
    # Generate multiple keypairs
    keypairs = [backend.keygen() for _ in range(3)]
    
    # All should have correct sizes
    for pk, sk in keypairs:
        assert len(pk) == 1952
        assert len(sk) == 4000
    
    # All should be different
    public_keys = [pk for pk, sk in keypairs]
    assert len(set(public_keys)) == len(public_keys)
    
    # Each can sign and verify
    message = b"test"
    for pk, sk in keypairs:
        sig = backend.sign(sk, message)
        assert backend.verify(pk, message, sig)
