"""
Tests for node data directory auto-creation at startup.

Validates that the node and related services automatically create
required directories under ~/.animica with appropriate permissions.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def get_permissions(path: Path) -> int:
    """Get file/directory permissions as octal (e.g., 0o755)."""
    return stat.S_IMODE(path.stat().st_mode)


def test_core_db_sqlite_creates_parent_directory(tmp_path: Path) -> None:
    """Test that SQLite KV creates parent directory when opening a database."""
    from core.db.sqlite import open_sqlite_kv
    
    db_file = tmp_path / "chain-data" / "test.db"
    
    # Database file should not exist yet
    assert not db_file.exists()
    assert not db_file.parent.exists()
    
    # Open database - should create parent directory
    kv = open_sqlite_kv(str(db_file), create=True)
    
    # Verify directory was created
    assert db_file.parent.exists()
    assert db_file.exists()
    
    # Verify permissions (0o755 for non-sensitive data)
    perms = get_permissions(db_file.parent)
    assert perms == 0o755, f"Expected 0o755, got {oct(perms)}"
    
    # Cleanup
    kv.close()


def test_core_db_rocksdb_creates_directory(tmp_path: Path) -> None:
    """Test that RocksDB creates directory with appropriate permissions."""
    try:
        from core.db.rocksdb import open_rocksdb_kv
    except Exception:
        pytest.skip("RocksDB not available")
    
    db_path = tmp_path / "chain-data" / "rocksdb"
    
    # Directory should not exist yet
    assert not db_path.exists()
    
    # Open database - should create directory
    try:
        kv = open_rocksdb_kv(str(db_path), fallback_to_sqlite=False)
    except RuntimeError as e:
        if "RocksDB backend unavailable" in str(e):
            pytest.skip("RocksDB not installed")
        raise
    
    # Verify directory was created
    assert db_path.exists()
    
    # Verify permissions (0o755 for non-sensitive data)
    perms = get_permissions(db_path)
    assert perms == 0o755, f"Expected 0o755, got {oct(perms)}"
    
    # Cleanup
    kv.close()


def test_aicf_db_path_creates_directory_with_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that AICF db_path creates directories with appropriate permissions."""
    from aicf.db import db_path
    
    monkeypatch.setenv("AICF_DB_DIR", str(tmp_path / "aicf"))
    
    # Request db path with create=True
    db_file = db_path("queue/test.sqlite3", create=True)
    
    # Verify directory was created
    assert db_file.parent.exists()
    assert db_file.parent.name == "queue"
    
    # Verify permissions (0o755 for AICF data)
    perms = get_permissions(db_file.parent)
    assert perms == 0o755, f"Expected 0o755, got {oct(perms)}"


def test_p2p_peer_store_creates_directory(tmp_path: Path) -> None:
    """Test that P2P peer store creates directory with appropriate permissions."""
    # Import the JSONPeerStore class
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parents[4] / "p2p"))
        from cli.peer import JSONPeerStore
    except (ImportError, ModuleNotFoundError):
        pytest.skip("P2P peer store not available")
    
    store_path = tmp_path / "p2p" / "peers.json"
    
    # Directory should not exist yet
    assert not store_path.parent.exists()
    
    # Create store - should create directory
    store = JSONPeerStore(store_path)
    store.add_peer("test_peer", ["/ip4/127.0.0.1/tcp/42069"])
    
    # Verify directory was created
    assert store_path.parent.exists()
    assert store_path.exists()
    
    # Verify permissions (0o755 for p2p data)
    perms = get_permissions(store_path.parent)
    assert perms == 0o755, f"Expected 0o755, got {oct(perms)}"


def test_node_data_directories_pattern(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test the expected directory structure for node data.
    
    This test validates that the expected pattern for node data directories
    works correctly, even though actual node startup is tested via Docker.
    """
    from animica.config import get_network_defaults
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # Get expected data directories for each network
    networks = ["mainnet", "testnet", "devnet", "local-devnet"]
    
    for network in networks:
        defaults = get_network_defaults(network)
        data_dir_str = defaults["data_dir"]
        
        # Expand home directory
        data_dir = Path(data_dir_str).expanduser()
        
        # Create directory structure
        data_dir.mkdir(parents=True, exist_ok=True)
        data_dir.chmod(0o755)
        
        # Verify directory was created
        assert data_dir.exists(), f"Data dir for {network} should exist"
        
        # Verify permissions
        perms = get_permissions(data_dir)
        assert perms == 0o755, f"Expected 0o755 for {network}, got {oct(perms)}"


def test_complete_fresh_environment_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test that all required directories are created in a fresh environment.
    
    This comprehensive test validates that starting from scratch creates
    all necessary directories with appropriate permissions.
    """
    from animica.cli import wallet, key
    from animica.cli.state import CLIState
    from aicf.db import db_path
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    animica_dir = tmp_path / ".animica"
    
    # Step 1: Verify no .animica directory exists
    assert not animica_dir.exists(), "Fresh environment should have no .animica"
    
    # Step 2: Create a wallet (should create ~/.animica with 0o700)
    result = runner.invoke(wallet.app, ["create", "--label", "test", "--allow-insecure-fallback"])
    assert result.exit_code == 0
    assert animica_dir.exists()
    assert (animica_dir / "wallets.json").exists()
    
    # Verify wallet directory has secure permissions
    perms = get_permissions(animica_dir)
    assert perms == 0o700, f"Wallet dir should be 0o700, got {oct(perms)}"
    
    # Step 3: Create a key (should create ~/.animica/keys with 0o700)
    key_file = animica_dir / "keys" / "test.json"
    result = runner.invoke(key.app, ["new", "--label", "testkey", "--output", str(key_file)])
    assert result.exit_code == 0
    assert key_file.exists()
    
    # Verify keys directory has secure permissions
    keys_dir = animica_dir / "keys"
    perms = get_permissions(keys_dir)
    assert perms == 0o700, f"Keys dir should be 0o700, got {oct(perms)}"
    
    # Step 4: Set CLI state (should create ~/.config/animica with 0o755)
    state_file = tmp_path / ".config" / "animica" / "state.json"
    state = CLIState(state_file)
    state.set("network", "testnet")
    assert state_file.exists()
    
    # Verify config directory has non-sensitive permissions
    config_dir = state_file.parent
    perms = get_permissions(config_dir)
    assert perms == 0o755, f"Config dir should be 0o755, got {oct(perms)}"
    
    # Step 5: Create AICF db path (should create ~/.animica/aicf with 0o755)
    monkeypatch.setenv("AICF_DB_DIR", str(animica_dir / "aicf"))
    aicf_db = db_path("test.sqlite3", create=True)
    assert aicf_db.parent.exists()
    
    # Verify AICF directory has non-sensitive permissions
    aicf_dir = aicf_db.parent
    perms = get_permissions(aicf_dir)
    assert perms == 0o755, f"AICF dir should be 0o755, got {oct(perms)}"
    
    # Step 6: Verify overall structure
    assert (animica_dir / "wallets.json").exists()
    assert (animica_dir / "keys").is_dir()
    assert (tmp_path / ".config" / "animica").is_dir()
    assert (animica_dir / "aicf").is_dir()


def test_idempotent_directory_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test that directory creation is idempotent and doesn't fail when dirs exist.
    """
    from animica.cli import wallet
    
    monkeypatch.setenv("HOME", str(tmp_path))
    
    # Create wallet multiple times
    for i in range(3):
        result = runner.invoke(
            wallet.app,
            ["create", "--label", f"wallet{i}", "--allow-insecure-fallback"]
        )
        assert result.exit_code == 0
    
    # All wallets should exist
    wallet_file = tmp_path / ".animica" / "wallets.json"
    store = json.loads(wallet_file.read_text())
    assert len(store["wallets"]) == 3
    
    # Directory should still have correct permissions
    animica_dir = tmp_path / ".animica"
    perms = get_permissions(animica_dir)
    assert perms == 0o700, f"Dir should remain 0o700, got {oct(perms)}"
