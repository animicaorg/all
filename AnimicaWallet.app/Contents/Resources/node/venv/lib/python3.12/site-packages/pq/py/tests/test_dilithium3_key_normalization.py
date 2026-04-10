"""
Tests for Dilithium3 secret key normalization (4000 vs 4032 bytes).

Ensures backward compatibility between:
- Canonical FIPS 204 format: 4000 bytes
- Legacy liboqs format: 4032 bytes
"""

import sys
import types
import pytest

# Mock oqs before importing sign module
sys.modules.setdefault(
    "oqs",
    types.SimpleNamespace(
        Signature=None,
        get_enabled_sig_mechanisms=lambda: [],
        get_enabled_mechanisms=lambda: [],
    ),
)

import pq.py.sign as sign


class TestDilithium3KeyNormalization:
    """Test Dilithium3 secret key normalization for 4000 vs 4032 bytes."""
    
    def test_normalize_canonical_4000_bytes(self):
        """Canonical 4000-byte key should pass through unchanged."""
        sk_canonical = b"x" * 4000
        result = sign._normalize_dilithium3_sk(sk_canonical)
        assert len(result) == 4000
        assert result == sk_canonical
    
    def test_normalize_legacy_4032_bytes(self):
        """Legacy 4032-byte key should be trimmed to 4000 bytes."""
        # Simulate liboqs format: 4000 bytes + 32 bytes metadata
        sk_core = b"y" * 4000
        sk_metadata = b"z" * 32
        sk_legacy = sk_core + sk_metadata
        
        assert len(sk_legacy) == 4032
        
        result = sign._normalize_dilithium3_sk(sk_legacy)
        assert len(result) == 4000
        assert result == sk_core
        # Verify metadata was stripped
        assert result != sk_legacy
    
    def test_normalize_invalid_length_raises(self):
        """Invalid key lengths should raise ValueError with helpful message."""
        sk_invalid = b"x" * 3999
        
        with pytest.raises(ValueError) as exc_info:
            sign._normalize_dilithium3_sk(sk_invalid)
        
        error_msg = str(exc_info.value)
        assert "3999" in error_msg
        assert "4000" in error_msg
        assert "4032" in error_msg
        assert "wallet doctor" in error_msg.lower()
    
    def test_normalize_empty_key_raises(self):
        """Empty key should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            sign._normalize_dilithium3_sk(b"")
        
        error_msg = str(exc_info.value)
        assert "0" in error_msg or "length" in error_msg.lower()
    
    def test_normalize_too_long_key_raises(self):
        """Key longer than 4032 bytes should raise ValueError."""
        sk_too_long = b"x" * 5000
        
        with pytest.raises(ValueError) as exc_info:
            sign._normalize_dilithium3_sk(sk_too_long)
        
        error_msg = str(exc_info.value)
        assert "5000" in error_msg


class TestBackendSignNormalization:
    """Test that _backend_sign automatically normalizes Dilithium3 keys."""
    
    def test_backend_sign_normalizes_4032_to_4000(self, monkeypatch):
        """_backend_sign should normalize 4032-byte keys before calling backend."""
        received_sk_len = None
        
        def fake_sign(sk: bytes, msg: bytes) -> bytes:
            nonlocal received_sk_len
            received_sk_len = len(sk)
            return b"signature"
        
        fake_backend = types.SimpleNamespace(sign=fake_sign, __name__="fake_dilithium3")
        
        # Monkeypatch the resolve backend function to return our fake backend
        def mock_resolve(alg_name):
            if alg_name == "dilithium3":
                return fake_backend
            raise NotImplementedError(f"No backend for {alg_name}")
        
        monkeypatch.setattr(sign, "_resolve_backend", mock_resolve)
        
        # Create legacy 4032-byte key
        sk_legacy = b"x" * 4032
        msg = b"test message"
        
        result = sign._backend_sign("dilithium3", sk_legacy, msg)
        
        # Backend should receive normalized 4000-byte key
        assert received_sk_len == 4000
        assert result == b"signature"
    
    def test_backend_sign_passes_canonical_4000_unchanged(self, monkeypatch):
        """_backend_sign should pass canonical 4000-byte keys unchanged."""
        received_sk = None
        
        def fake_sign(sk: bytes, msg: bytes) -> bytes:
            nonlocal received_sk
            received_sk = sk
            return b"signature"
        
        fake_backend = types.SimpleNamespace(sign=fake_sign, __name__="fake_dilithium3")
        
        def mock_resolve(alg_name):
            if alg_name == "dilithium3":
                return fake_backend
            raise NotImplementedError(f"No backend for {alg_name}")
        
        monkeypatch.setattr(sign, "_resolve_backend", mock_resolve)
        
        # Create canonical 4000-byte key
        sk_canonical = b"y" * 4000
        msg = b"test message"
        
        result = sign._backend_sign("dilithium3", sk_canonical, msg)
        
        # Backend should receive the same key
        assert received_sk == sk_canonical
        assert len(received_sk) == 4000
        assert result == b"signature"
    
    def test_backend_sign_raises_on_invalid_dilithium3_key(self, monkeypatch):
        """_backend_sign should raise ValueError for invalid Dilithium3 key lengths."""
        fake_backend = types.SimpleNamespace(sign=lambda sk, msg: b"sig", __name__="fake_dilithium3")
        
        def mock_resolve(alg_name):
            if alg_name == "dilithium3":
                return fake_backend
            raise NotImplementedError(f"No backend for {alg_name}")
        
        monkeypatch.setattr(sign, "_resolve_backend", mock_resolve)
        
        sk_invalid = b"x" * 3999
        msg = b"test message"
        
        with pytest.raises(ValueError) as exc_info:
            sign._backend_sign("dilithium3", sk_invalid, msg)
        
        error_msg = str(exc_info.value)
        assert "3999" in error_msg
    
    def test_backend_sign_does_not_normalize_other_algs(self, monkeypatch):
        """_backend_sign should not normalize keys for non-Dilithium3 algorithms."""
        received_sk_len = None
        
        def fake_sign(sk: bytes, msg: bytes) -> bytes:
            nonlocal received_sk_len
            received_sk_len = len(sk)
            return b"signature"
        
        fake_backend = types.SimpleNamespace(sign=fake_sign, __name__="fake_sphincs")
        
        def mock_resolve(alg_name):
            if alg_name == "sphincs_shake_128s":
                return fake_backend
            raise NotImplementedError(f"No backend for {alg_name}")
        
        monkeypatch.setattr(sign, "_resolve_backend", mock_resolve)
        
        # SPHINCS+ has different key size - should pass through unchanged
        sk = b"x" * 64  # SPHINCS+ sk is 64 bytes
        msg = b"test message"
        
        result = sign._backend_sign("sphincs_shake_128s", sk, msg)
        
        # Backend should receive original key unchanged
        assert received_sk_len == 64
        assert result == b"signature"


class TestSignDetachedWithNormalization:
    """Integration test: sign_detached should work with both key formats."""
    
    def test_sign_detached_with_4032_byte_key(self, monkeypatch):
        """sign_detached should successfully sign with legacy 4032-byte key."""
        def fake_sign(sk: bytes, msg: bytes) -> bytes:
            # Verify we received normalized 4000-byte key
            assert len(sk) == 4000
            return b"signature_bytes"
        
        fake_backend = types.SimpleNamespace(sign=fake_sign, __name__="fake_dilithium3")
        
        def mock_resolve(alg_name):
            if alg_name == "dilithium3":
                return fake_backend
            raise NotImplementedError(f"No backend for {alg_name}")
        
        monkeypatch.setattr(sign, "_resolve_backend", mock_resolve)
        
        # Legacy 4032-byte key (simulates liboqs output)
        sk_legacy = b"x" * 4032
        msg = b"hello animica"
        
        sig = sign.sign_detached(msg, "dilithium3", sk_legacy, domain="test")
        
        assert sig.alg_name == "dilithium3"
        assert sig.domain == "test"
        assert sig.sig == b"signature_bytes"
    
    def test_sign_detached_with_4000_byte_key(self, monkeypatch):
        """sign_detached should successfully sign with canonical 4000-byte key."""
        def fake_sign(sk: bytes, msg: bytes) -> bytes:
            assert len(sk) == 4000
            return b"signature_bytes"
        
        fake_backend = types.SimpleNamespace(sign=fake_sign, __name__="fake_dilithium3")
        
        def mock_resolve(alg_name):
            if alg_name == "dilithium3":
                return fake_backend
            raise NotImplementedError(f"No backend for {alg_name}")
        
        monkeypatch.setattr(sign, "_resolve_backend", mock_resolve)
        
        # Canonical 4000-byte key
        sk_canonical = b"y" * 4000
        msg = b"hello animica"
        
        sig = sign.sign_detached(msg, "dilithium3", sk_canonical, domain="test")
        
        assert sig.alg_name == "dilithium3"
        assert sig.sig == b"signature_bytes"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
