"""
Tests for PQ utilities module.

Tests cover:
- Detection of liboqs-python availability
- Error message generation
- Environment variable handling
- Logging behavior
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from animica.cli.pq_utils import (
    check_pq_signing_available,
    ensure_pq_signing_or_exit,
    get_pq_missing_error_message,
)


class TestCheckPQSigningAvailable:
    """Tests for check_pq_signing_available function."""

    def test_unsafe_fake_mode_enabled(self):
        """Test that unsafe fake mode is detected and returns True."""
        with patch.dict(os.environ, {"ANIMICA_UNSAFE_PQ_FAKE": "1"}):
            available, error = check_pq_signing_available()
            assert available is True
            assert error is None

    def test_liboqs_python_not_installed(self):
        """Test behavior when liboqs-python is not installed."""
        # Remove ANIMICA_UNSAFE_PQ_FAKE if set
        with patch.dict(os.environ, {}, clear=True):
            # Mock the import to raise ImportError
            import importlib
            original_import = importlib.__import__
            
            def mock_import(name, *args, **kwargs):
                if name == "oqs":
                    raise ImportError("No module named 'oqs'")
                return original_import(name, *args, **kwargs)
            
            with patch("builtins.__import__", side_effect=mock_import):
                with patch("animica.cli.pq_utils.logger") as mock_logger:
                    available, error = check_pq_signing_available()
                    assert available is False
                    assert error is None

    def test_liboqs_python_installed_sphincs_enabled(self):
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
            
            # We need to ensure the import statement in the function uses our mock
            import importlib
            original_import = importlib.__import__
            
            def mock_import(name, *args, **kwargs):
                if name == "oqs":
                    return mock_oqs
                return original_import(name, *args, **kwargs)
            
            with patch("builtins.__import__", side_effect=mock_import):
                with patch("animica.cli.pq_utils.logger") as mock_logger:
                    available, error = check_pq_signing_available()
                    assert available is True
                    assert error is None
                    # Verify logging - check if info was called with version message
                    info_calls = [str(call) for call in mock_logger.info.call_args_list]
                    assert any("0.10.0" in call for call in info_calls)
                    assert any("SPHINCS+" in call for call in info_calls)

    def test_liboqs_python_installed_sphincs_disabled(self):
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
            
            import importlib
            original_import = importlib.__import__
            
            def mock_import(name, *args, **kwargs):
                if name == "oqs":
                    return mock_oqs
                return original_import(name, *args, **kwargs)
            
            with patch("builtins.__import__", side_effect=mock_import):
                with patch("animica.cli.pq_utils.logger") as mock_logger:
                    available, error = check_pq_signing_available()
                    assert available is False
                    assert error is not None
                    assert "SPHINCS+-SHAKE-128s is not enabled" in error
                    # Verify error logging
                    mock_logger.error.assert_called_once()

    def test_liboqs_python_installed_no_version(self):
        """Test when liboqs-python doesn't expose version info."""
        # Remove ANIMICA_UNSAFE_PQ_FAKE if set
        with patch.dict(os.environ, {}, clear=True):
            # Create a mock oqs module without __version__
            mock_oqs = MagicMock()
            del mock_oqs.__version__
            mock_oqs.get_enabled_sig_mechanisms.return_value = [
                "SPHINCS+-SHAKE-128s",
            ]
            
            import importlib
            original_import = importlib.__import__
            
            def mock_import(name, *args, **kwargs):
                if name == "oqs":
                    return mock_oqs
                return original_import(name, *args, **kwargs)
            
            with patch("builtins.__import__", side_effect=mock_import):
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
