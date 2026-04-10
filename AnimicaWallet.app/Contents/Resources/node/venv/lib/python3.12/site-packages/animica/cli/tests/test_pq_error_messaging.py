"""
Integration tests for PQ error messaging improvements.

These tests verify that the enhanced error messages and logging work correctly
when invoked through the CLI.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from animica.cli.wallet import app as wallet_app


runner = CliRunner(mix_stderr=False)


class TestPQErrorMessaging:
    """Test enhanced PQ error messages in CLI context."""

    def test_create_wallet_without_pq_shows_helpful_error(self, tmp_path):
        """Test that wallet create without PQ shows helpful error message."""
        wallet_file = tmp_path / "wallets.json"
        
        # Ensure PQ is not available by not setting insecure fallback
        with patch.dict(os.environ, {}, clear=True):
            result = runner.invoke(
                wallet_app,
                [
                    "--wallet-file", str(wallet_file),
                    "create",
                    "--label", "test",
                ],
            )
            
            # Should exit with error
            assert result.exit_code != 0
            
            # Should contain helpful error message
            assert "Post-quantum signing dependencies not available" in result.stderr
            assert "0.14." in result.stderr
            assert "liboqs-python" in result.stderr
            assert "pip install liboqs-python" in result.stderr

    def test_error_message_includes_env_vars_when_set(self, tmp_path):
        """Test that error message includes environment variables when set."""
        wallet_file = tmp_path / "wallets.json"
        
        # Set some library path variables
        with patch.dict(
            os.environ,
            {
                "LD_LIBRARY_PATH": "/custom/lib",
                "LIBOQS_PATH": "/custom/liboqs.so",
            },
            clear=True,
        ):
            result = runner.invoke(
                wallet_app,
                [
                    "--wallet-file", str(wallet_file),
                    "create",
                    "--label", "test",
                ],
            )
            
            # Should mention the env vars we set
            assert "LD_LIBRARY_PATH" in result.stderr
            assert "/custom/lib" in result.stderr
            assert "LIBOQS_PATH" in result.stderr
            assert "/custom/liboqs.so" in result.stderr

    def test_create_wallet_with_insecure_fallback_works(self, tmp_path):
        """Test that wallet create works with insecure fallback."""
        wallet_file = tmp_path / "wallets.json"
        
        result = runner.invoke(
            wallet_app,
            [
                "--wallet-file", str(wallet_file),
                "create",
                "--label", "test-fallback",
                "--allow-insecure-fallback",
            ],
        )
        
        # Should succeed
        assert result.exit_code == 0
        assert "Wallet created" in result.stdout
        assert "test-fallback" in result.stdout
        
        # Wallet file should exist
        assert wallet_file.exists()

    def test_error_message_shows_setup_script_hint(self, tmp_path):
        """Test that error message mentions setup.sh env.sh file."""
        wallet_file = tmp_path / "wallets.json"
        
        with patch.dict(os.environ, {}, clear=True):
            result = runner.invoke(
                wallet_app,
                [
                    "--wallet-file", str(wallet_file),
                    "create",
                    "--label", "test",
                ],
            )
            
            # Should mention the env.sh script from setup.sh
            assert "env.sh" in result.stderr or "setup.sh" in result.stderr

    def test_error_message_platform_specific(self, tmp_path):
        """Test that error message shows platform-specific instructions."""
        wallet_file = tmp_path / "wallets.json"
        
        with patch.dict(os.environ, {}, clear=True):
            result = runner.invoke(
                wallet_app,
                [
                    "--wallet-file", str(wallet_file),
                    "create",
                    "--label", "test",
                ],
            )
            
            # Should mention either LD_LIBRARY_PATH (Linux) or DYLD_LIBRARY_PATH (macOS)
            # At least one should be present
            assert (
                "LD_LIBRARY_PATH" in result.stderr
                or "DYLD_LIBRARY_PATH" in result.stderr
            )
