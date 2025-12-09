"""
Tests for automatic directory creation under ~/.animica.

This module verifies that all CLI commands properly create their required
directories with appropriate permissions when they don't exist.
"""
import json
import os
import stat
from pathlib import Path
from typing import Optional

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def allow_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable PQ fallback for all tests."""
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")


def get_permissions(path: Path) -> int:
    """Get file/directory permissions as octal."""
    return stat.S_IMODE(path.stat().st_mode)


def test_wallet_creates_animica_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that wallet commands create ~/.animica directory when it doesn't exist."""
    from animica.cli import wallet
    
    # Set HOME to tmp_path - no .animica directory exists
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # Create a wallet - should auto-create ~/.animica/wallets.json
    result = runner.invoke(wallet.app, ["create", "--label", "test", "--allow-insecure-fallback"])
    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert "Wallet created" in result.output
    
    # Verify directory and file were created
    animica_dir = tmp_path / ".animica"
    wallet_file = animica_dir / "wallets.json"
    
    assert animica_dir.exists(), "~/.animica directory should be auto-created"
    assert animica_dir.is_dir(), "~/.animica should be a directory"
    assert wallet_file.exists(), "wallets.json should be created"
    
    # Verify file content
    store = json.loads(wallet_file.read_text())
    assert "wallets" in store
    assert len(store["wallets"]) == 1
    assert store["wallets"][0]["label"] == "test"


def test_wallet_file_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that wallet files are created with secure permissions (0o600)."""
    from animica.cli import wallet
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    result = runner.invoke(wallet.app, ["create", "--label", "secure", "--allow-insecure-fallback"])
    assert result.exit_code == 0
    
    wallet_file = tmp_path / ".animica" / "wallets.json"
    perms = get_permissions(wallet_file)
    
    # File should be readable/writable by owner only (0o600)
    assert perms == 0o600, f"Wallet file permissions should be 0o600, got {oct(perms)}"


def test_wallet_directory_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that .animica directory is created with secure permissions (0o700)."""
    from animica.cli import wallet
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    result = runner.invoke(wallet.app, ["create", "--label", "secure", "--allow-insecure-fallback"])
    assert result.exit_code == 0
    
    animica_dir = tmp_path / ".animica"
    perms = get_permissions(animica_dir)
    
    # Directory should be accessible by owner only (0o700)
    assert perms == 0o700, f".animica directory permissions should be 0o700, got {oct(perms)}"


def test_key_output_creates_keys_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that key output creates ~/.animica/keys directory when it doesn't exist."""
    from animica.cli import key
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    keys_dir = tmp_path / ".animica" / "keys"
    output_file = keys_dir / "test.json"
    
    # Generate key with output to non-existent directory
    result = runner.invoke(key.app, ["new", "--label", "testkey", "--output", str(output_file)])
    assert result.exit_code == 0, f"Command failed: {result.output}"
    
    # Verify directory and file were created
    assert keys_dir.exists(), "~/.animica/keys directory should be auto-created"
    assert keys_dir.is_dir(), "~/.animica/keys should be a directory"
    assert output_file.exists(), "key file should be created"
    
    # Verify key content
    key_data = json.loads(output_file.read_text())
    assert key_data["label"] == "testkey"
    assert "public_key_hex" in key_data
    assert "secret_key_hex" in key_data


def test_key_file_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that key files are created with secure permissions (0o600)."""
    from animica.cli import key
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    output_file = tmp_path / ".animica" / "keys" / "secure.json"
    result = runner.invoke(key.app, ["new", "--label", "secure", "--output", str(output_file)])
    assert result.exit_code == 0
    
    perms = get_permissions(output_file)
    
    # Key file should be readable/writable by owner only (0o600)
    assert perms == 0o600, f"Key file permissions should be 0o600, got {oct(perms)}"


def test_key_directory_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that ~/.animica/keys directory is created with secure permissions (0o700)."""
    from animica.cli import key
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    keys_dir = tmp_path / ".animica" / "keys"
    output_file = keys_dir / "secure.json"
    result = runner.invoke(key.app, ["new", "--label", "secure", "--output", str(output_file)])
    assert result.exit_code == 0
    
    perms = get_permissions(keys_dir)
    
    # Keys directory should be accessible by owner only (0o700)
    assert perms == 0o700, f"Keys directory permissions should be 0o700, got {oct(perms)}"


def test_key_list_handles_missing_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that 'key list' handles non-existent keys directory gracefully."""
    from animica.cli import key
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # List keys when ~/.animica/keys doesn't exist
    result = runner.invoke(key.app, ["list"])
    assert result.exit_code == 0
    assert "No keys directory found" in result.output


def test_wallet_export_creates_output_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that wallet export creates the output directory if it doesn't exist."""
    from animica.cli import wallet
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # Create a wallet first
    result = runner.invoke(wallet.app, ["create", "--label", "export_test", "--allow-insecure-fallback"])
    assert result.exit_code == 0
    
    # Get the wallet address
    wallet_file = tmp_path / ".animica" / "wallets.json"
    store = json.loads(wallet_file.read_text())
    address = store["wallets"][0]["address"]
    
    # Export to a non-existent directory
    export_dir = tmp_path / "exports" / "nested"
    export_file = export_dir / "wallet.json"
    
    result = runner.invoke(wallet.app, ["export", address, "--out", str(export_file)])
    assert result.exit_code == 0, f"Export failed: {result.output}"
    
    # Verify directory was created
    assert export_dir.exists(), "Export directory should be auto-created"
    assert export_file.exists(), "Export file should be created"


def test_state_creates_config_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that CLI state creates ~/.config/animica directory when it doesn't exist."""
    from animica.cli.state import CLIState
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    state_file = tmp_path / ".config" / "animica" / "state.json"
    
    # Create state and save a value
    state = CLIState(state_file)
    state.set("network", "testnet")
    
    # Verify directory and file were created
    config_dir = tmp_path / ".config" / "animica"
    assert config_dir.exists(), "~/.config/animica directory should be auto-created"
    assert state_file.exists(), "state.json should be created"
    
    # Verify content
    data = json.loads(state_file.read_text())
    assert data["network"] == "testnet"


def test_multiple_commands_idempotent_directory_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that multiple commands can create directories idempotently."""
    from animica.cli import wallet
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # Create first wallet
    result1 = runner.invoke(wallet.app, ["create", "--label", "wallet1", "--allow-insecure-fallback"])
    assert result1.exit_code == 0
    
    animica_dir = tmp_path / ".animica"
    assert animica_dir.exists()
    
    # Create second wallet - directory already exists
    result2 = runner.invoke(wallet.app, ["create", "--label", "wallet2", "--allow-insecure-fallback"])
    assert result2.exit_code == 0
    
    # Both wallets should exist
    wallet_file = animica_dir / "wallets.json"
    store = json.loads(wallet_file.read_text())
    assert len(store["wallets"]) == 2
    assert store["wallets"][0]["label"] == "wallet1"
    assert store["wallets"][1]["label"] == "wallet2"


def test_aicf_db_path_creates_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that AICF db_path helper creates directories when create=True."""
    from aicf.db import db_path
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # Request db path with create=True
    db_file = db_path("test.sqlite3", create=True)
    
    # Verify directory was created
    assert db_file.parent.exists(), "AICF db directory should be auto-created"
    assert db_file.parent.name == "aicf"


def test_fresh_environment_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test complete workflow in a fresh environment (no ~/.animica)."""
    from animica.cli import wallet, key
    from animica.cli.state import CLIState
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # Verify no .animica directory exists
    animica_dir = tmp_path / ".animica"
    assert not animica_dir.exists(), "Fresh environment should have no .animica"
    
    # Step 1: Create a wallet
    result = runner.invoke(wallet.app, ["create", "--label", "main", "--allow-insecure-fallback"])
    assert result.exit_code == 0
    assert animica_dir.exists()
    
    # Step 2: Create a key
    key_file = animica_dir / "keys" / "main.json"
    result = runner.invoke(key.app, ["new", "--label", "mainkey", "--output", str(key_file)])
    assert result.exit_code == 0
    assert key_file.exists()
    
    # Step 3: Set CLI state (use explicit path)
    state_file = tmp_path / ".config" / "animica" / "state.json"
    state = CLIState(state_file)
    state.set("network", "mainnet")
    assert state_file.exists()
    
    # Verify all directories were created
    assert (animica_dir / "wallets.json").exists()
    assert (animica_dir / "keys").is_dir()
    assert (tmp_path / ".config" / "animica").is_dir()


def test_directory_creation_with_custom_paths(tmp_path: Path) -> None:
    """Test directory creation works with custom paths (not ~/.animica)."""
    from animica.cli import wallet
    
    custom_wallet = tmp_path / "custom" / "location" / "wallets.json"
    
    # Create wallet at custom location
    result = runner.invoke(wallet.app, ["--wallet-file", str(custom_wallet), "create", "--label", "custom", "--allow-insecure-fallback"])
    assert result.exit_code == 0
    
    # Verify custom directory was created
    assert custom_wallet.parent.exists()
    assert custom_wallet.exists()
    
    store = json.loads(custom_wallet.read_text())
    assert store["wallets"][0]["label"] == "custom"
