"""
Integration tests for chain ID behavior.

These tests validate the actual chain ID resolution behavior without mocking.
"""

import subprocess
import json
import sys


def test_cli_chain_id_explicit_flag():
    """Test that explicit --chain-id flag works."""
    result = subprocess.run(
        [sys.executable, "-m", "omni_sdk.cli.main", "--chain-id", "999", "env"],
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = json.loads(result.stdout)
    assert output["chain_id"] == 999


def test_cli_chain_id_env_var():
    """Test that OMNI_CHAIN_ID environment variable works."""
    import os
    env = os.environ.copy()
    env["OMNI_CHAIN_ID"] = "777"
    
    result = subprocess.run(
        [sys.executable, "-m", "omni_sdk.cli.main", "env"],
        capture_output=True,
        text=True,
        env=env,
    )
    
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = json.loads(result.stdout)
    assert output["chain_id"] == 777


def test_cli_chain_id_never_zero():
    """Test that chain ID never defaults to 0."""
    import os
    env = os.environ.copy()
    # Clear any chain ID env vars
    env.pop("OMNI_CHAIN_ID", None)
    
    result = subprocess.run(
        [sys.executable, "-m", "omni_sdk.cli.main", "env"],
        capture_output=True,
        text=True,
        env=env,
    )
    
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = json.loads(result.stdout)
    
    # Chain ID should never be 0
    assert output["chain_id"] != 0, f"Chain ID should not be 0, got: {output['chain_id']}"
    # Should be a valid positive integer
    assert output["chain_id"] > 0


def test_cli_version_works():
    """Test that version command works without requiring chain ID."""
    result = subprocess.run(
        [sys.executable, "-m", "omni_sdk.cli.main", "version"],
        capture_output=True,
        text=True,
    )
    
    assert result.returncode == 0
    assert "omni-sdk" in result.stdout


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
