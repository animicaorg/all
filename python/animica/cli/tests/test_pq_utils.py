"""
Tests for PQ utilities module.

Tests cover:
- Detection of liboqs-python availability
- Error message generation
- Environment variable handling
- Logging behavior
"""

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from animica.cli.pq_utils import (
    check_pq_signing_available,
    ensure_pq_signing_or_exit,
    get_pq_missing_error_message,
)


@pytest.fixture
def mock_oqs_import():
    """
    Helper fixture to mock the oqs module import.
    
    Returns a context manager that can be used to mock the import behavior.
    """
    def _mock_import(mock_oqs_module=None):
        """
        Context manager to mock oqs import.
        
        Args:
            mock_oqs_module: If provided, return this when oqs is imported.
                           If None, raise ImportError.
        """
        original_import = importlib.__import__
        
        def mock_import_func(name, *args, **kwargs):
            if name == "oqs":
                if mock_oqs_module is None:
                    raise ImportError("No module named 'oqs'")
                return mock_oqs_module
            return original_import(name, *args, **kwargs)
        
        return patch("builtins.__import__", side_effect=mock_import_func)
    
    return _mock_import


class TestCheckPQSigningAvailable:
    """Tests for check_pq_signing_available function."""

    def test_unsafe_fake_mode_enabled(self):
        """Test that unsafe fake mode is detected and returns True."""
        with patch.dict(os.environ, {"ANIMICA_UNSAFE_PQ_FAKE": "1"}):
            available, error = check_pq_signing_available()
            assert available is True
            assert error is None

    def test_liboqs_python_not_installed(self, mock_oqs_import):
        """Test behavior when liboqs-python is not installed."""
        # Remove ANIMICA_UNSAFE_PQ_FAKE if set
        with patch.dict(os.environ, {}, clear=True):
            with mock_oqs_import(None):
                with patch("animica.cli.pq_utils.logger") as mock_logger:
                    available, error = check_pq_signing_available()
                    assert available is False
                    assert error is None

    def test_liboqs_python_installed_sphincs_enabled(self, mock_oqs_import):
        """Test successful detection when liboqs-python is properly installed."""
        # Remove ANIMICA_UNSAFE_PQ_FAKE if set
        with patch.dict(os.environ, {}, clear=True):
            # Create a mock oqs module with SPHINCS+ enabled
            mock_oqs = MagicMock()
            mock_oqs.__version__ = "0.10.0"
            mock_oqs.get_enabled_sig_mechanisms.return_value = [
                "Dilithium3",
                "SPHINCS+-SHAKE-128s",
                "Falcon-512",
            ]
            
            with mock_oqs_import(mock_oqs):
                with patch("animica.cli.pq_utils.logger") as mock_logger:
                    available, error = check_pq_signing_available()
                    assert available is True
                    assert error is None
                    # Verify logging - check if info was called with version message
                    info_calls = [str(call) for call in mock_logger.info.call_args_list]
                    assert any("0.10.0" in call for call in info_calls)
                    assert any("SPHINCS+" in call for call in info_calls)

    def test_liboqs_python_installed_sphincs_simple_variant(self, mock_oqs_import):
        """Test successful detection with liboqs 0.15.0+ simple variant."""
        # Remove ANIMICA_UNSAFE_PQ_FAKE if set
        with patch.dict(os.environ, {}, clear=True):
            # Create a mock oqs module with SPHINCS+-simple enabled (liboqs 0.15.0+)
            mock_oqs = MagicMock()
            mock_oqs.__version__ = "0.15.0"
            mock_oqs.get_enabled_sig_mechanisms.return_value = [
                "Dilithium3",
                "SPHINCS+-SHAKE-128s-simple",  # New naming in liboqs 0.15.0+
                "Falcon-512",
            ]
            
            with mock_oqs_import(mock_oqs):
                with patch("animica.cli.pq_utils.logger") as mock_logger:
                    available, error = check_pq_signing_available()
                    assert available is True
                    assert error is None
                    # Verify logging - check if info was called with version message
                    info_calls = [str(call) for call in mock_logger.info.call_args_list]
                    assert any("0.15.0" in call for call in info_calls)
                    assert any("SPHINCS+" in call for call in info_calls)

    def test_liboqs_python_installed_sphincs_disabled(self, mock_oqs_import):
        """Test detection when liboqs-python is installed but SPHINCS+ is missing."""
        # Remove ANIMICA_UNSAFE_PQ_FAKE if set
        with patch.dict(os.environ, {}, clear=True):
            # Create a mock oqs module without SPHINCS+
            mock_oqs = MagicMock()
            mock_oqs.__version__ = "0.10.0"
            mock_oqs.get_enabled_sig_mechanisms.return_value = [
                "Dilithium3",
                "Falcon-512",
            ]
            
            with mock_oqs_import(mock_oqs):
                with patch("animica.cli.pq_utils.logger") as mock_logger:
                    available, error = check_pq_signing_available()
                    assert available is False
                    assert error is not None
                    assert "SPHINCS+-SHAKE-128s is not enabled" in error
                    # Verify error logging
                    mock_logger.error.assert_called_once()

    def test_liboqs_python_installed_no_version(self, mock_oqs_import):
        """Test when liboqs-python doesn't expose version info."""
        # Remove ANIMICA_UNSAFE_PQ_FAKE if set
        with patch.dict(os.environ, {}, clear=True):
            # Create a mock oqs module without __version__
            mock_oqs = MagicMock()
            del mock_oqs.__version__
            mock_oqs.get_enabled_sig_mechanisms.return_value = [
                "SPHINCS+-SHAKE-128s",
            ]
            
            with mock_oqs_import(mock_oqs):
                with patch("animica.cli.pq_utils.logger") as mock_logger:
                    available, error = check_pq_signing_available()
                    assert available is True
                    assert error is None
                    # Should still log detection with "unknown" version
                    info_calls = [str(call) for call in mock_logger.info.call_args_list]
                    assert any("unknown" in call for call in info_calls)


class TestGetPQMissingErrorMessage:
    """Tests for get_pq_missing_error_message function."""

    def test_error_message_basic(self):
        """Test that error message contains essential information."""
        with patch.dict(os.environ, {}, clear=True):
            msg = get_pq_missing_error_message()
            
            # Check for key content
            assert "Post-quantum signing dependencies not available" in msg
            assert "liboqs-python" in msg
            assert "v0.15.0" in msg  # Should reference correct version
            assert "pip install liboqs-python" in msg
            assert "ANIMICA_UNSAFE_PQ_FAKE" in msg

    def test_error_message_with_ld_library_path(self):
        """Test that error message includes LD_LIBRARY_PATH if set."""
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/custom/lib:/usr/local/lib"}):
            msg = get_pq_missing_error_message()
            
            assert "LD_LIBRARY_PATH" in msg
            assert "/custom/lib:/usr/local/lib" in msg

    def test_error_message_with_dyld_library_path(self):
        """Test that error message includes DYLD_LIBRARY_PATH if set (macOS)."""
        with patch.dict(os.environ, {"DYLD_LIBRARY_PATH": "/opt/homebrew/lib"}):
            msg = get_pq_missing_error_message()
            
            assert "DYLD_LIBRARY_PATH" in msg
            assert "/opt/homebrew/lib" in msg

    def test_error_message_with_liboqs_path(self):
        """Test that error message includes LIBOQS_PATH if set."""
        with patch.dict(os.environ, {"LIBOQS_PATH": "/path/to/liboqs.so"}):
            msg = get_pq_missing_error_message()
            
            assert "LIBOQS_PATH" in msg
            assert "/path/to/liboqs.so" in msg

    def test_error_message_with_multiple_paths(self):
        """Test that error message includes all relevant environment variables."""
        with patch.dict(
            os.environ,
            {
                "LD_LIBRARY_PATH": "/lib1",
                "DYLD_LIBRARY_PATH": "/lib2",
                "LIBRARY_PATH": "/lib3",
                "LIBOQS_PATH": "/lib4/liboqs.so",
            },
        ):
            msg = get_pq_missing_error_message()
            
            assert "LD_LIBRARY_PATH" in msg
            assert "DYLD_LIBRARY_PATH" in msg
            assert "LIBRARY_PATH" in msg
            assert "LIBOQS_PATH" in msg


class TestEnsurePQSigningOrExit:
    """Tests for ensure_pq_signing_or_exit function."""

    def test_exits_when_not_available(self, capsys):
        """Test that function exits when PQ signing is not available."""
        with patch("animica.cli.pq_utils.check_pq_signing_available", return_value=(False, None)):
            with pytest.raises(SystemExit) as exc_info:
                ensure_pq_signing_or_exit()
            
            assert exc_info.value.code == 1
            
            # Check that error message was printed to stderr
            captured = capsys.readouterr()
            assert "Post-quantum signing dependencies not available" in captured.err

    def test_exits_with_specific_error(self, capsys):
        """Test that function exits and shows specific error message."""
        with patch(
            "animica.cli.pq_utils.check_pq_signing_available",
            return_value=(False, "SPHINCS+ not enabled"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                ensure_pq_signing_or_exit()
            
            assert exc_info.value.code == 1
            
            # Check that specific error was printed
            captured = capsys.readouterr()
            assert "SPHINCS+ not enabled" in captured.err

    def test_does_not_exit_when_available(self):
        """Test that function does not exit when PQ signing is available."""
        with patch("animica.cli.pq_utils.check_pq_signing_available", return_value=(True, None)):
            # Should not raise SystemExit
            ensure_pq_signing_or_exit()


class TestGetPQDiagnostics:
    """Tests for get_pq_diagnostics function."""

    def test_diagnostics_when_oqs_not_available(self):
        """Test diagnostic output when python-oqs is not installed."""
        from animica.cli.pq_utils import get_pq_diagnostics
        
        with patch.dict(os.environ, {}, clear=True):
            diag = get_pq_diagnostics()
            
            # Should contain diagnostic header
            assert "PQ Library Diagnostics" in diag
            assert "Environment Variables:" in diag
            
    def test_diagnostics_shows_env_variables(self):
        """Test that diagnostics shows environment variables."""
        from animica.cli.pq_utils import get_pq_diagnostics
        
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/test/lib", "LIBOQS_PATH": "/test/liboqs.so"}):
            diag = get_pq_diagnostics()
            
            assert "LD_LIBRARY_PATH" in diag
            assert "/test/lib" in diag
            assert "LIBOQS_PATH" in diag
            assert "/test/liboqs.so" in diag

    def test_diagnostics_with_oqs_available(self, mock_oqs_import):
        """Test diagnostic output when python-oqs is available."""
        from animica.cli.pq_utils import get_pq_diagnostics
        
        mock_oqs = MagicMock()
        mock_oqs.__version__ = "0.10.0"
        mock_oqs.get_enabled_sig_mechanisms.return_value = ["SPHINCS+-SHAKE-128s"]
        
        with patch.dict(os.environ, {}, clear=True):
            with mock_oqs_import(mock_oqs):
                diag = get_pq_diagnostics()
                
                # Should show oqs is available
                assert "python-oqs" in diag
                assert "0.10.0" in diag
                assert "SPHINCS+" in diag

    def test_diagnostics_with_oqs_simple_variant(self, mock_oqs_import):
        """Test diagnostic output with liboqs 0.15.0+ simple variant."""
        from animica.cli.pq_utils import get_pq_diagnostics
        
        mock_oqs = MagicMock()
        mock_oqs.__version__ = "0.15.0"
        mock_oqs.get_enabled_sig_mechanisms.return_value = [
            "Dilithium3",
            "SPHINCS+-SHAKE-128s-simple"
        ]
        
        with patch.dict(os.environ, {}, clear=True):
            with mock_oqs_import(mock_oqs):
                diag = get_pq_diagnostics()
                
                # Should show oqs is available with simple variant
                assert "python-oqs" in diag
                assert "0.15.0" in diag
                assert "SPHINCS+" in diag
                assert "simple" in diag


class TestCheckPQSigningWithBackend:
    """Tests for check_pq_signing_available with oqs_backend fallback."""

    @staticmethod
    def _mock_oqs_import_error():
        """Helper to create a mock import that raises ImportError for oqs module."""
        def mock_import(name, *args, **kwargs):
            if name == "oqs":
                raise ImportError("No module named 'oqs'")
            return __import__(name, *args, **kwargs)
        return mock_import

    def test_falls_back_to_oqs_backend_when_oqs_module_missing(self):
        """Test that check falls back to oqs_backend when oqs module not available."""
        from animica.cli.pq_utils import check_pq_signing_available
        
        # Mock oqs module as not available
        with patch.dict(os.environ, {}, clear=True):
            with patch("animica.cli.pq_utils.logger"):
                # Mock oqs_backend as available
                mock_backend = MagicMock()
                mock_backend.is_available.return_value = True
                mock_backend.get_version_info.return_value = "0.15.0"
                
                with patch("animica.cli.pq_utils.oqs_backend", mock_backend):
                    with patch("builtins.__import__", side_effect=self._mock_oqs_import_error()):
                        available, error = check_pq_signing_available()
                        
                        # Should be available via backend
                        assert available is True
                        assert error is None

    def test_reports_unavailable_when_both_missing(self):
        """Test that unavailable is reported when both oqs and backend missing."""
        from animica.cli.pq_utils import check_pq_signing_available
        
        with patch.dict(os.environ, {}, clear=True):
            with patch("animica.cli.pq_utils.logger"):
                # Mock both as unavailable
                mock_backend = MagicMock()
                mock_backend.is_available.return_value = False
                
                with patch("animica.cli.pq_utils.oqs_backend", mock_backend):
                    with patch("builtins.__import__", side_effect=self._mock_oqs_import_error()):
                        available, error = check_pq_signing_available()
                        
                        # Should be unavailable
                        assert available is False
