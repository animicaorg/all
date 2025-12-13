"""
Tests for Dilithium3 wallet key format normalization.

Ensures wallets store canonical 4000-byte keys while maintaining
backward compatibility with legacy 4032-byte keys from liboqs.
"""

import pytest


class TestDilithium3WalletKeyNormalization:
    """Test wallet-level normalization of Dilithium3 keys."""
    
    def test_normalize_canonical_4000_bytes(self):
        """Canonical 4000-byte key should remain unchanged."""
        from animica.cli.wallet import _normalize_dilithium3_secret_key
        
        sk = b"x" * 4000
        result = _normalize_dilithium3_secret_key(sk, "dilithium3")
        
        assert len(result) == 4000
        assert result == sk
    
    def test_normalize_legacy_4032_bytes(self):
        """Legacy 4032-byte key should be normalized to 4000 bytes."""
        from animica.cli.wallet import _normalize_dilithium3_secret_key
        
        sk_core = b"y" * 4000
        sk_metadata = b"z" * 32
        sk_legacy = sk_core + sk_metadata
        
        result = _normalize_dilithium3_secret_key(sk_legacy, "dilithium3")
        
        assert len(result) == 4000
        assert result == sk_core
    
    def test_normalize_ml_dsa_65_alias(self):
        """ML-DSA-65 should be treated same as dilithium3."""
        from animica.cli.wallet import _normalize_dilithium3_secret_key
        
        sk_legacy = b"x" * 4032
        
        result = _normalize_dilithium3_secret_key(sk_legacy, "ml-dsa-65")
        assert len(result) == 4000
        
        result = _normalize_dilithium3_secret_key(sk_legacy, "mldsa65")
        assert len(result) == 4000
    
    def test_normalize_other_alg_unchanged(self):
        """Non-Dilithium3 algorithms should pass through unchanged."""
        from animica.cli.wallet import _normalize_dilithium3_secret_key
        
        # SPHINCS+ has 64-byte secret key
        sk_sphincs = b"s" * 64
        result = _normalize_dilithium3_secret_key(sk_sphincs, "sphincs_shake_128s")
        
        assert result == sk_sphincs
        assert len(result) == 64
    
    def test_normalize_invalid_dilithium3_key_returns_unchanged(self):
        """Invalid Dilithium3 key length should return unchanged (signing will fail)."""
        from animica.cli.wallet import _normalize_dilithium3_secret_key
        
        sk_invalid = b"x" * 3999
        result = _normalize_dilithium3_secret_key(sk_invalid, "dilithium3")
        
        # Returns unchanged - error will occur during signing
        assert result == sk_invalid
    
    def test_normalize_case_insensitive(self):
        """Algorithm name comparison should be case-insensitive."""
        from animica.cli.wallet import _normalize_dilithium3_secret_key
        
        sk_legacy = b"x" * 4032
        
        result1 = _normalize_dilithium3_secret_key(sk_legacy, "DILITHIUM3")
        result2 = _normalize_dilithium3_secret_key(sk_legacy, "Dilithium3")
        result3 = _normalize_dilithium3_secret_key(sk_legacy, "dilithium3")
        
        assert result1 == result2 == result3
        assert len(result1) == 4000


class TestWalletGenerationStorageFormat:
    """Test that wallet generation stores canonical key format."""
    
    def test_normalized_secret_key_is_canonical(self):
        """Verify normalization reduces 4032-byte key to 4000 bytes."""
        from animica.cli.wallet import _normalize_dilithium3_secret_key
        
        # Simulate liboqs-generated 4032-byte key
        sk_liboqs = b"s" * 4032
        
        # Normalize for storage
        sk_normalized = _normalize_dilithium3_secret_key(sk_liboqs, "dilithium3")
        
        # Should be canonical 4000 bytes
        assert len(sk_normalized) == 4000
        assert sk_normalized == b"s" * 4000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
