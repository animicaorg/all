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
    
    def test_generated_wallet_stores_canonical_format(self, monkeypatch):
        """New wallets should store canonical 4000-byte Dilithium3 keys."""
        from animica.cli.wallet import _generate_entry
        
        # Mock keygen_sig to return 4032-byte key (like liboqs)
        class MockKeyPair:
            def __init__(self):
                self.alg_id = 0x1001  # dilithium3
                self.alg_name = "dilithium3"
                self.public_key = b"p" * 1952
                self.secret_key = b"s" * 4032  # Legacy liboqs format
                self.address = "anim1test"
        
        def mock_keygen_sig(alg_id):
            return MockKeyPair()
        
        # Patch keygen and registry
        import sys
        import types
        
        mock_pq = types.SimpleNamespace(
            keygen=types.SimpleNamespace(keygen_sig=mock_keygen_sig),
            address=types.SimpleNamespace(address_from_pubkey=lambda pk, aid: "anim1test"),
            registry=types.SimpleNamespace(
                default_signature_alg=lambda: types.SimpleNamespace(
                    alg_id=0x1001, name="dilithium3"
                ),
                name_of=lambda aid: "dilithium3",
            ),
        )
        
        monkeypatch.setitem(sys.modules, "pq.py", mock_pq)
        monkeypatch.setitem(sys.modules, "pq.py.keygen", mock_pq.keygen)
        monkeypatch.setitem(sys.modules, "pq.py.address", mock_pq.address)
        monkeypatch.setitem(sys.modules, "pq.py.registry", mock_pq.registry)
        
        # Force HAVE_PQ = True in wallet module
        import animica.cli.wallet as wallet_module
        monkeypatch.setattr(wallet_module, "HAVE_PQ", True)
        monkeypatch.setattr(wallet_module, "keygen_sig", mock_keygen_sig)
        
        # Generate wallet entry
        entry = _generate_entry("test-wallet", allow_fallback=False)
        
        # Verify stored secret key is canonical 4000 bytes
        stored_sk = bytes.fromhex(entry.secret_key_hex)
        assert len(stored_sk) == 4000, f"Expected 4000 bytes, got {len(stored_sk)}"
        
        # Verify it's the normalized version (first 4000 bytes of 4032)
        assert stored_sk == b"s" * 4000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
