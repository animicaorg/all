"""
Tests for OQS backend loader and library path handling.

These tests verify:
- Library loading from different paths
- Environment variable handling (LD_LIBRARY_PATH, DYLD_LIBRARY_PATH, LIBOQS_PATH)
- Proper logging of library loading attempts
- Error messages when library is not found
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestLiboqsLoader:
    """Tests for _load_liboqs function."""

    def test_load_from_liboqs_path_env(self):
        """Test loading liboqs from LIBOQS_PATH environment variable."""
        from pq.py.algs import oqs_backend
        
        # Create a mock CDLL
        mock_lib = MagicMock()
        
        with patch.dict(os.environ, {"LIBOQS_PATH": "/custom/path/liboqs.so"}):
            with patch("os.path.exists", return_value=True):
                with patch("ctypes.CDLL", return_value=mock_lib) as mock_cdll:
                    with patch.object(oqs_backend, "logger") as mock_logger:
                        lib = oqs_backend._load_liboqs()
                        
                        # Verify CDLL was called with correct path
                        mock_cdll.assert_called_once_with("/custom/path/liboqs.so")
                        assert lib is mock_lib
                        
                        # Verify logging
                        mock_logger.info.assert_called()

    def test_load_from_liboqs_path_not_found(self):
        """Test behavior when LIBOQS_PATH points to non-existent file."""
        from pq.py.algs import oqs_backend
        
        with patch.dict(os.environ, {"LIBOQS_PATH": "/nonexistent/liboqs.so"}):
            with patch("os.path.exists", return_value=False):
                with patch.object(oqs_backend, "logger") as mock_logger:
                    lib = oqs_backend._load_liboqs()
                    
                    # Should log warning and continue searching
                    assert any("does not exist" in str(call) for call in mock_logger.warning.call_args_list)
                    # Will return None if no other candidates found
                    assert lib is None

    def test_load_from_find_library(self):
        """Test loading via find_library() when LIBOQS_PATH not set."""
        from pq.py.algs import oqs_backend
        
        mock_lib = MagicMock()
        
        with patch.dict(os.environ, {}, clear=True):
            with patch("ctypes.util.find_library", return_value="/usr/lib/liboqs.so"):
                with patch("ctypes.CDLL", return_value=mock_lib) as mock_cdll:
                    with patch.object(oqs_backend, "logger") as mock_logger:
                        lib = oqs_backend._load_liboqs()
                        
                        # Should try to load from find_library result
                        assert lib is mock_lib
                        mock_logger.info.assert_called()

    def test_load_fallback_to_common_names(self):
        """Test fallback to common library names."""
        from pq.py.algs import oqs_backend
        
        mock_lib = MagicMock()
        
        with patch.dict(os.environ, {}, clear=True):
            with patch("ctypes.util.find_library", return_value=None):
                # First attempt fails, second succeeds
                with patch("ctypes.CDLL", side_effect=[OSError("not found"), mock_lib]) as mock_cdll:
                    with patch.object(oqs_backend, "logger") as mock_logger:
                        lib = oqs_backend._load_liboqs()
                        
                        # Should eventually find the library
                        assert lib is mock_lib
                        # Should have tried multiple candidates
                        assert mock_cdll.call_count >= 2

    def test_load_failure_logs_warning(self):
        """Test that failure to load logs appropriate warning."""
        from pq.py.algs import oqs_backend
        
        with patch.dict(os.environ, {}, clear=True):
            with patch("ctypes.util.find_library", return_value=None):
                with patch("ctypes.CDLL", side_effect=OSError("not found")):
                    with patch.object(oqs_backend, "logger") as mock_logger:
                        lib = oqs_backend._load_liboqs()
                        
                        assert lib is None
                        # Should log warning about not finding library
                        assert any(
                            "not found" in str(call).lower()
                            for call in mock_logger.warning.call_args_list
                        )

    def test_logs_ld_library_path_when_set(self):
        """Test that LD_LIBRARY_PATH is logged when set (Linux)."""
        from pq.py.algs import oqs_backend
        
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/custom/lib"}):
            with patch("ctypes.util.find_library", return_value=None):
                with patch("ctypes.CDLL", side_effect=OSError("not found")):
                    with patch.object(oqs_backend, "logger") as mock_logger:
                        with patch.object(sys, "platform", "linux"):
                            lib = oqs_backend._load_liboqs()
                            
                            # Should log the LD_LIBRARY_PATH
                            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
                            assert any("LD_LIBRARY_PATH" in call for call in debug_calls)

    def test_logs_dyld_library_path_when_set(self):
        """Test that DYLD_LIBRARY_PATH is logged when set (macOS)."""
        from pq.py.algs import oqs_backend
        
        with patch.dict(os.environ, {"DYLD_LIBRARY_PATH": "/opt/homebrew/lib"}):
            with patch("ctypes.util.find_library", return_value=None):
                with patch("ctypes.CDLL", side_effect=OSError("not found")):
                    with patch.object(oqs_backend, "logger") as mock_logger:
                        with patch.object(sys, "platform", "darwin"):
                            lib = oqs_backend._load_liboqs()
                            
                            # Should log the DYLD_LIBRARY_PATH
                            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
                            assert any("DYLD_LIBRARY_PATH" in call for call in debug_calls)


class TestOQSBackendInit:
    """Tests for OQSBackend initialization."""

    def test_init_fails_with_helpful_error(self):
        """Test that OQSBackend.__init__ provides helpful error when library not loaded."""
        # Need to mock _HAVE to be False
        from pq.py.algs import oqs_backend
        
        with patch.object(oqs_backend, "_HAVE", False):
            with pytest.raises(RuntimeError) as exc_info:
                oqs_backend.OQSBackend()
            
            error_msg = str(exc_info.value)
            # Check for key troubleshooting information
            assert "liboqs shared library not found" in error_msg
            assert "v0.15.0" in error_msg
            assert "apt-get install liboqs-dev" in error_msg or "brew install liboqs" in error_msg
            assert "LD_LIBRARY_PATH" in error_msg or "DYLD_LIBRARY_PATH" in error_msg
            assert "LIBOQS_PATH" in error_msg

    def test_init_success_logs_version(self):
        """Test that successful initialization logs version info."""
        from pq.py.algs import oqs_backend
        
        # Mock successful library load
        mock_lib = MagicMock()
        mock_lib.OQS_version.return_value = b"0.15.0"
        
        with patch.object(oqs_backend, "_HAVE", True):
            with patch.object(oqs_backend, "_LIB", mock_lib):
                with patch.object(oqs_backend, "get_version_info", return_value="0.15.0"):
                    with patch.object(oqs_backend, "logger") as mock_logger:
                        backend = oqs_backend.OQSBackend()
                        
                        # Should log version
                        mock_logger.info.assert_called_once()
                        assert "0.15.0" in str(mock_logger.info.call_args)


class TestGetVersionInfo:
    """Tests for get_version_info function."""

    def test_returns_none_when_not_available(self):
        """Test that get_version_info returns None when liboqs not loaded."""
        from pq.py.algs import oqs_backend
        
        with patch.object(oqs_backend, "_HAVE", False):
            version = oqs_backend.get_version_info()
            assert version is None

    def test_returns_version_string(self):
        """Test that get_version_info returns version string when available."""
        from pq.py.algs import oqs_backend
        
        mock_lib = MagicMock()
        mock_lib.OQS_version.return_value = b"0.15.0"
        
        with patch.object(oqs_backend, "_HAVE", True):
            with patch.object(oqs_backend, "_LIB", mock_lib):
                with patch.object(oqs_backend, "logger"):
                    version = oqs_backend.get_version_info()
                    assert version == "0.15.0"

    def test_returns_unknown_on_error(self):
        """Test that get_version_info returns 'unknown' if version call fails."""
        from pq.py.algs import oqs_backend
        
        mock_lib = MagicMock()
        # Simulate library without OQS_version function
        del mock_lib.OQS_version
        
        with patch.object(oqs_backend, "_HAVE", True):
            with patch.object(oqs_backend, "_LIB", mock_lib):
                with patch.object(oqs_backend, "logger"):
                    version = oqs_backend.get_version_info()
                    assert version == "unknown"


class TestIsAvailable:
    """Tests for is_available function."""

    def test_returns_true_when_loaded(self):
        """Test that is_available returns True when library is loaded."""
        from pq.py.algs import oqs_backend
        
        with patch.object(oqs_backend, "_HAVE", True):
            assert oqs_backend.is_available() is True

    def test_returns_false_when_not_loaded(self):
        """Test that is_available returns False when library is not loaded."""
        from pq.py.algs import oqs_backend
        
        with patch.object(oqs_backend, "_HAVE", False):
            assert oqs_backend.is_available() is False
