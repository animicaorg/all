"""
Integration tests for liboqs loading improvements.

These tests verify that the enhanced liboqs loading mechanism works correctly
across different installation scenarios and provides proper error messages.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestLiboqsLoadingIntegration:
    """Integration tests for liboqs loading across the stack."""

    def test_wallet_create_without_liboqs_shows_helpful_error(self, tmp_path):
        """Test that wallet create without liboqs shows enhanced error message."""
        # Import after conftest sets up environment
        from animica.cli.wallet import app
        from typer.testing import CliRunner
        
        wallet_file = tmp_path / "test_wallet.json"
        runner = CliRunner(mix_stderr=False)
        
        # Ensure PQ is not available (no fake mode)
        with patch.dict(os.environ, {}, clear=True):
            result = runner.invoke(
                app,
                ["--wallet-file", str(wallet_file), "create", "--label", "test"],
            )
            
            # Should exit with error
            assert result.exit_code != 0
            
            # Should contain enhanced error message
            assert "Post-quantum signing dependencies not available" in result.stderr
            assert "PQ Library Diagnostics" in result.stderr
            assert "Environment Variables:" in result.stderr
            
            # Should mention both oqs module and backend
            assert "python-oqs" in result.stderr
            assert "liboqs" in result.stderr

    def test_wallet_create_with_fake_mode_works(self, tmp_path):
        """Test that wallet create works with fake mode enabled."""
        from animica.cli.wallet import app
        from typer.testing import CliRunner
        
        wallet_file = tmp_path / "test_wallet.json"
        runner = CliRunner(mix_stderr=False)
        
        # Enable fake mode
        with patch.dict(os.environ, {"ANIMICA_UNSAFE_PQ_FAKE": "1"}):
            result = runner.invoke(
                app,
                [
                    "--wallet-file", str(wallet_file),
                    "create",
                    "--label", "test-fake",
                    "--allow-insecure-fallback",
                ],
            )
            
            # Should succeed
            assert result.exit_code == 0
            assert "Wallet created" in result.stdout
            assert "test-fake" in result.stdout
            
            # Wallet file should exist
            assert wallet_file.exists()

    def test_diagnostics_shows_env_variables(self):
        """Test that diagnostics show environment variables when set."""
        from animica.cli.pq_utils import get_pq_diagnostics
        
        test_env = {
            "LD_LIBRARY_PATH": "/test/lib1:/test/lib2",
            "LIBOQS_PATH": "/custom/liboqs.so",
            "ANIMICA_UNSAFE_PQ_FAKE": "0",
        }
        
        with patch.dict(os.environ, test_env):
            diag = get_pq_diagnostics()
            
            # Should show all set variables
            assert "LD_LIBRARY_PATH" in diag
            assert "/test/lib1:/test/lib2" in diag
            assert "LIBOQS_PATH" in diag
            assert "/custom/liboqs.so" in diag
            assert "ANIMICA_UNSAFE_PQ_FAKE" in diag

    def test_check_pq_falls_back_to_backend(self):
        """Test that PQ checking falls back to oqs_backend when oqs module unavailable."""
        from animica.cli.pq_utils import check_pq_signing_available
        
        # This test verifies that when oqs module is not available,
        # the code checks oqs_backend. In this environment both are unavailable,
        # so we verify the fallback path exists without errors.
        
        with patch.dict(os.environ, {}, clear=True):
            available, error = check_pq_signing_available()
            
            # Without liboqs installed, should be unavailable
            # but shouldn't crash (verifies fallback path works)
            assert available is False
            assert error is None

    def test_bundled_lib_path_detection(self):
        """Test that python-oqs bundled library paths are detected."""
        from pq.py.algs import oqs_backend
        
        # Mock an oqs module location
        mock_spec = MagicMock()
        mock_spec.origin = "/usr/local/lib/python3.12/site-packages/oqs/__init__.py"
        
        # Mock glob to return some bundled libs
        bundled_libs = [
            "/usr/local/lib/python3.12/site-packages/oqs/liboqs.so.5",
            "/usr/local/lib/python3.12/site-packages/oqs/.libs/liboqs.so.5",
        ]
        
        with patch("importlib.util.find_spec", return_value=mock_spec):
            with patch("glob.glob", return_value=bundled_libs):
                with patch("os.path.exists", return_value=True):
                    paths = oqs_backend._get_python_oqs_bundled_lib_paths()
                    
                    # Should find bundled paths
                    assert len(paths) > 0
                    assert any("site-packages/oqs" in p for p in paths)

    def test_error_message_includes_setup_hints(self):
        """Test that error messages include setup.sh hints."""
        from animica.cli.pq_utils import get_pq_missing_error_message
        
        with patch.dict(os.environ, {}, clear=True):
            msg = get_pq_missing_error_message()
            
            # Should mention setup script and vendored path
            assert "setup.sh" in msg
            assert ".deps/liboqs/0.14.0" in msg
            assert "0.14." in msg

    def test_check_pq_accepts_pinned_version(self):
        """Test that PQ checking works with pinned liboqs 0.14.x."""
        from animica.cli.pq_utils import check_pq_signing_available

        # Mock oqs module with pinned variants
        mock_oqs = MagicMock()
        mock_oqs.__version__ = "0.14.0"
        mock_oqs.get_enabled_sig_mechanisms.return_value = [
            "Dilithium3",
            "SPHINCS+-SHAKE-128s",
            "Falcon-512",
        ]
        
        with patch.dict(os.environ, {}, clear=True):
            # Use sys.modules to mock the oqs import
            with patch.dict('sys.modules', {'oqs': mock_oqs}):
                available, error = check_pq_signing_available()
                
                # Should detect SPHINCS+ with simple variant
                assert available is True
                assert error is None


class TestLiboqsLoadingSequence:
    """Test the complete loading sequence."""

    def test_load_sequence_with_liboqs_path(self):
        """Test that LIBOQS_PATH has highest priority."""
        from pq.py.algs import oqs_backend
        
        mock_lib = MagicMock()
        
        with patch.dict(os.environ, {"LIBOQS_PATH": "/explicit/liboqs.so"}):
            with patch("os.path.exists", return_value=True):
                with patch("ctypes.CDLL", return_value=mock_lib) as mock_cdll:
                    with patch.object(oqs_backend, "logger"):
                        lib = oqs_backend._load_liboqs()
                        
                        # Should load from LIBOQS_PATH first
                        assert lib is mock_lib
                        mock_cdll.assert_called_with("/explicit/liboqs.so")

    def test_load_sequence_tries_bundled_before_system(self):
        """Test that bundled paths are tried before system paths."""
        from pq.py.algs import oqs_backend
        
        mock_lib = MagicMock()
        bundled_path = "/usr/lib/python3/site-packages/oqs/liboqs.so.5"
        
        # Mock finding bundled lib
        mock_spec = MagicMock()
        mock_spec.origin = "/usr/lib/python3/site-packages/oqs/__init__.py"
        
        with patch.dict(os.environ, {}, clear=True):
            with patch("importlib.util.find_spec", return_value=mock_spec):
                with patch("glob.glob", return_value=[bundled_path]):
                    with patch("ctypes.CDLL") as mock_cdll:
                        # First call (bundled) succeeds
                        mock_cdll.return_value = mock_lib
                        
                        with patch.object(oqs_backend, "logger"):
                            lib = oqs_backend._load_liboqs()
                            
                            # Should load bundled lib
                            assert lib is mock_lib
                            # First call should be to bundled path
                            first_call = mock_cdll.call_args_list[0]
                            assert bundled_path in str(first_call)

    def test_load_failure_provides_detailed_info(self):
        """Test that load failure provides comprehensive diagnostic info."""
        from pq.py.algs import oqs_backend
        
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/custom/lib"}):
            with patch("importlib.util.find_spec", return_value=None):
                with patch("ctypes.util.find_library", return_value=None):
                    with patch("ctypes.CDLL", side_effect=OSError("not found")):
                        with patch.object(oqs_backend, "logger") as mock_logger:
                            lib = oqs_backend._load_liboqs()
                            
                            # Should return None
                            assert lib is None
                            
                            # Should have logged detailed warning
                            warning_calls = [
                                str(call) for call in mock_logger.warning.call_args_list
                            ]
                            assert any("not found after searching" in call for call in warning_calls)
                            assert any("LD_LIBRARY_PATH" in call for call in warning_calls)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
