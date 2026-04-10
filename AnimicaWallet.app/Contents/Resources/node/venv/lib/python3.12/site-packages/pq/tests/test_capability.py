"""
Tests for PQ capability detection module.

Covers:
- Capability detection with caching
- Mechanism selection (ML-DSA, Dilithium, SPHINCS+)
- Environment variable handling (ANIMICA_PQ_MECHANISM)
- Fallback behavior when PQ not available
- Diagnostics output
"""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_capability_cache():
    """Reset capability cache before each test."""
    from pq.py.capability import reset_cache
    reset_cache()
    yield
    reset_cache()


class TestCapabilityDetection:
    """Tests for capability detection."""

    def test_detect_via_oqs_module_pinned(self):
        """Test detection when oqs module exposes pinned Dilithium mechanisms."""
        from pq.py.capability import detect_capability

        # Mock oqs module with Dilithium
        mock_oqs = MagicMock()
        mock_oqs.__version__ = "0.14.0"
        mock_oqs.get_enabled_sig_mechanisms.return_value = [
            "Dilithium3",
            "SPHINCS+-SHAKE-128s",
        ]
        mock_oqs.get_enabled_kem_mechanisms.return_value = ["Kyber768"]

        with patch.dict("sys.modules", {"oqs": mock_oqs}):
            cap = detect_capability()

            assert cap.available is True
            assert cap.provider == "oqs"
            assert cap.version == "0.14.0"
            assert "Dilithium3" in cap.sig_mechanisms
            assert cap.default_sig_mechanism == "Dilithium3"

    def test_detect_via_oqs_module_dilithium3(self):
        """Test detection when oqs module has legacy Dilithium3."""
        from pq.py.capability import detect_capability
        
        # Mock oqs module with Dilithium3 (legacy)
        mock_oqs = MagicMock()
        mock_oqs.__version__ = "0.10.0"
        mock_oqs.get_enabled_sig_mechanisms.return_value = [
            "Dilithium3",
            "SPHINCS+-SHAKE-128s",
        ]
        mock_oqs.get_enabled_kem_mechanisms.return_value = ["Kyber768"]
        
        with patch.dict("sys.modules", {"oqs": mock_oqs}):
            cap = detect_capability()
            
            assert cap.available is True
            assert cap.provider == "oqs"
            assert "Dilithium3" in cap.sig_mechanisms
            assert cap.default_sig_mechanism == "Dilithium3"

    def test_detect_via_fake_mode(self):
        """Test detection when ANIMICA_UNSAFE_PQ_FAKE=1."""
        from pq.py.capability import detect_capability
        
        # Mock oqs import failure
        with patch.dict("sys.modules", {"oqs": None}):
            with patch("pq.py.capability._detect_oqs_module", return_value=None):
                with patch("pq.py.capability._detect_oqs_backend", return_value=None):
                    with patch.dict(os.environ, {"ANIMICA_UNSAFE_PQ_FAKE": "1"}):
                        cap = detect_capability()
                        
                        assert cap.available is True
                        assert cap.provider == "fake"
                        assert "dilithium3" in cap.sig_mechanisms
                        assert cap.default_sig_mechanism == "dilithium3"

    def test_detect_unavailable(self):
        """Test detection when PQ not available."""
        from pq.py.capability import detect_capability
        
        # Mock all detection methods to fail
        with patch("pq.py.capability._detect_oqs_module", return_value=None):
            with patch("pq.py.capability._detect_oqs_backend", return_value=None):
                with patch("pq.py.capability._detect_fake_fallback", return_value=None):
                    cap = detect_capability()
                    
                    assert cap.available is False
                    assert cap.provider == "none"
                    assert len(cap.sig_mechanisms) == 0
                    assert cap.default_sig_mechanism is None

    def test_caching(self):
        """Test that capability detection is cached."""
        from pq.py.capability import detect_capability, get_capability
        
        with patch("pq.py.capability._detect_oqs_module") as mock_detect:
            mock_detect.return_value = None
            with patch("pq.py.capability._detect_oqs_backend", return_value=None):
                with patch("pq.py.capability._detect_fake_fallback", return_value=None):
                    # First call should detect
                    cap1 = detect_capability()
                    assert mock_detect.call_count == 1
                    
                    # Second call should use cache
                    cap2 = get_capability()
                    assert mock_detect.call_count == 1  # Not called again
                    
                    # Should be same object
                    assert cap1 is cap2


class TestMechanismSelection:
    """Tests for default mechanism selection."""

    def test_select_mldsa65_preferred(self):
        """Test that ML-DSA-65 is preferred over Dilithium3."""
        from pq.py.capability import _select_default_sig_mechanism
        
        mechanisms = {"ML-DSA-65", "Dilithium3", "SPHINCS+-SHAKE-128s-simple"}
        default = _select_default_sig_mechanism(mechanisms)
        
        assert default == "ML-DSA-65"

    def test_select_mldsa87_if_no_65(self):
        """Test that ML-DSA-87 is selected if ML-DSA-65 not available."""
        from pq.py.capability import _select_default_sig_mechanism
        
        mechanisms = {"ML-DSA-87", "SPHINCS+-SHAKE-128s-simple"}
        default = _select_default_sig_mechanism(mechanisms)
        
        assert default == "ML-DSA-87"

    def test_select_dilithium3_legacy(self):
        """Test that Dilithium3 is selected for legacy liboqs."""
        from pq.py.capability import _select_default_sig_mechanism
        
        mechanisms = {"Dilithium3", "SPHINCS+-SHAKE-128s"}
        default = _select_default_sig_mechanism(mechanisms)
        
        assert default == "Dilithium3"

    def test_select_sphincs_if_no_dilithium(self):
        """Test that SPHINCS+ is selected if no Dilithium/ML-DSA available."""
        from pq.py.capability import _select_default_sig_mechanism
        
        mechanisms = {"SPHINCS+-SHAKE-128s-simple", "Falcon-512"}
        default = _select_default_sig_mechanism(mechanisms)
        
        assert default == "SPHINCS+-SHAKE-128s-simple"

    def test_select_with_env_var(self):
        """Test that ANIMICA_PQ_MECHANISM overrides default."""
        from pq.py.capability import _select_default_sig_mechanism
        
        mechanisms = {"ML-DSA-65", "ML-DSA-87", "SPHINCS+-SHAKE-128s-simple"}
        
        with patch.dict(os.environ, {"ANIMICA_PQ_MECHANISM": "ML-DSA-87"}):
            default = _select_default_sig_mechanism(mechanisms)
            assert default == "ML-DSA-87"
        
        with patch.dict(os.environ, {"ANIMICA_PQ_MECHANISM": "SPHINCS+-SHAKE-128s-simple"}):
            default = _select_default_sig_mechanism(mechanisms)
            assert default == "SPHINCS+-SHAKE-128s-simple"

    def test_select_with_env_var_case_insensitive(self):
        """Test that ANIMICA_PQ_MECHANISM is case-insensitive."""
        from pq.py.capability import _select_default_sig_mechanism
        
        mechanisms = {"ML-DSA-65"}
        
        with patch.dict(os.environ, {"ANIMICA_PQ_MECHANISM": "ml-dsa-65"}):
            default = _select_default_sig_mechanism(mechanisms)
            assert default == "ML-DSA-65"
        
        with patch.dict(os.environ, {"ANIMICA_PQ_MECHANISM": "MLDSA65"}):
            default = _select_default_sig_mechanism(mechanisms)
            assert default == "ML-DSA-65"

    def test_select_with_env_var_not_available(self):
        """Test behavior when ANIMICA_PQ_MECHANISM specifies unavailable mechanism."""
        from pq.py.capability import _select_default_sig_mechanism
        
        mechanisms = {"ML-DSA-65", "SPHINCS+-SHAKE-128s-simple"}
        
        with patch.dict(os.environ, {"ANIMICA_PQ_MECHANISM": "Falcon-512"}):
            # Should fall back to priority list (ML-DSA-65)
            default = _select_default_sig_mechanism(mechanisms)
            assert default == "ML-DSA-65"

    def test_select_empty_mechanisms(self):
        """Test selection with no mechanisms available."""
        from pq.py.capability import _select_default_sig_mechanism
        
        mechanisms = set()
        default = _select_default_sig_mechanism(mechanisms)
        
        assert default is None


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_is_pq_available(self):
        """Test is_pq_available helper."""
        from pq.py.capability import is_pq_available
        
        with patch("pq.py.capability.get_capability") as mock_get:
            mock_cap = MagicMock()
            mock_cap.available = True
            mock_get.return_value = mock_cap
            
            assert is_pq_available() is True
            
            mock_cap.available = False
            assert is_pq_available() is False

    def test_get_default_sig_mechanism(self):
        """Test get_default_sig_mechanism helper."""
        from pq.py.capability import get_default_sig_mechanism
        
        with patch("pq.py.capability.get_capability") as mock_get:
            mock_cap = MagicMock()
            mock_cap.default_sig_mechanism = "ML-DSA-65"
            mock_get.return_value = mock_cap
            
            assert get_default_sig_mechanism() == "ML-DSA-65"

    def test_get_available_sig_mechanisms(self):
        """Test get_available_sig_mechanisms helper."""
        from pq.py.capability import get_available_sig_mechanisms
        
        with patch("pq.py.capability.get_capability") as mock_get:
            mock_cap = MagicMock()
            mock_cap.sig_mechanisms = {"Dilithium3", "SPHINCS+-SHAKE-128s"}
            mock_get.return_value = mock_cap

            mechanisms = get_available_sig_mechanisms()
            assert "Dilithium3" in mechanisms
            assert "SPHINCS+-SHAKE-128s" in mechanisms


class TestDiagnostics:
    """Tests for diagnostics output."""

    def test_diagnostics_available(self):
        """Test diagnostics when PQ is available."""
        from pq.py.capability import get_diagnostics, PQCapability
        
        mock_cap = PQCapability(
            available=True,
            sig_mechanisms={"Dilithium3", "SPHINCS+-SHAKE-128s"},
            kem_mechanisms={"Kyber768"},
            default_sig_mechanism="Dilithium3",
            provider="oqs",
            version="0.14.0",
        )
        
        with patch("pq.py.capability.get_capability", return_value=mock_cap):
            diag = get_diagnostics()
            
            assert "Available: True" in diag
            assert "Provider: oqs" in diag
            assert "Version: 0.14.0" in diag
            assert "Dilithium3" in diag
            assert "SPHINCS+-SHAKE-128s" in diag

    def test_diagnostics_unavailable(self):
        """Test diagnostics when PQ is not available."""
        from pq.py.capability import get_diagnostics, PQCapability
        
        mock_cap = PQCapability(
            available=False,
            sig_mechanisms=set(),
            kem_mechanisms=set(),
            default_sig_mechanism=None,
            provider="none",
            version=None,
        )
        
        with patch("pq.py.capability.get_capability", return_value=mock_cap):
            diag = get_diagnostics()
            
            assert "Available: False" in diag
            assert "Provider: none" in diag
            assert "not available" in diag.lower()

    def test_diagnostics_env_vars(self):
        """Test that diagnostics includes environment variables."""
        from pq.py.capability import get_diagnostics
        
        with patch.dict(os.environ, {
            "ANIMICA_PQ_MECHANISM": "ML-DSA-65",
            "LIBOQS_PATH": "/path/to/liboqs.so",
        }):
            diag = get_diagnostics()
            
            assert "ANIMICA_PQ_MECHANISM" in diag
            assert "ML-DSA-65" in diag
            assert "LIBOQS_PATH" in diag
            assert "/path/to/liboqs.so" in diag


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
