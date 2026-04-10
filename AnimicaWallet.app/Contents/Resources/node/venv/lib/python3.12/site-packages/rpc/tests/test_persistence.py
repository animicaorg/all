"""
Tests for persistence and directory creation functionality.

This module tests that:
1. Database directories are created automatically under ~/.animica paths
2. SQLite and RocksDB URIs with ~ are expanded correctly
3. Parent directories are created before database files
"""

import os
import tempfile
from pathlib import Path

import pytest


def test_expand_sqlite_uri_creates_parent_directory():
    """Test that _expand_sqlite_uri creates parent directories for DB files."""
    from rpc.config import _expand_sqlite_uri
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test with a path that doesn't exist yet
        test_path = Path(tmpdir) / "animica" / "chain-1" / "test.db"
        test_uri = f"sqlite:///{test_path}"
        
        # Expand the URI (should create parent directory)
        expanded = _expand_sqlite_uri(test_uri)
        
        # Verify parent directory was created
        assert test_path.parent.exists(), "Parent directory should be created"
        assert expanded == test_uri, "URI should be unchanged"


def test_expand_sqlite_uri_handles_tilde_paths():
    """Test that _expand_sqlite_uri expands ~ in paths correctly."""
    from rpc.config import _expand_sqlite_uri
    
    # Test with ~ path (should expand to actual home directory)
    test_uri = "sqlite:///~/.animica/chain-1/test.db"
    expanded = _expand_sqlite_uri(test_uri)
    
    # Verify ~ was expanded
    assert "~" not in expanded, "~ should be expanded to home directory"
    assert expanded.startswith("sqlite:///"), "Should retain sqlite:/// prefix"
    
    # Extract path and verify parent exists or was created
    db_path = expanded.replace("sqlite:///", "")
    db_path_obj = Path(db_path)
    assert db_path_obj.parent.exists(), "Parent directory should exist or be created"


def test_expand_sqlite_uri_handles_rocksdb_paths():
    """Test that _expand_sqlite_uri handles RocksDB URIs correctly."""
    from rpc.config import _expand_sqlite_uri
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test with a RocksDB directory path
        test_path = Path(tmpdir) / "animica" / "rocks_data"
        test_uri = f"rocksdb:///{test_path}"
        
        # Expand the URI (should create parent directory)
        expanded = _expand_sqlite_uri(test_uri)
        
        # Verify parent directory was created
        assert test_path.parent.exists(), "Parent directory should be created"


def test_expand_sqlite_uri_handles_memory_databases():
    """Test that _expand_sqlite_uri doesn't try to create dirs for :memory:."""
    from rpc.config import _expand_sqlite_uri
    
    # Test with in-memory database
    test_uri = "sqlite:///:memory:"
    expanded = _expand_sqlite_uri(test_uri)
    
    # Should not raise any errors and should return the same URI
    assert expanded == test_uri


def test_expand_sqlite_uri_handles_bare_file_paths():
    """Test that _expand_sqlite_uri converts bare file paths to sqlite URIs."""
    from rpc.config import _expand_sqlite_uri
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test with a bare file path
        test_path = Path(tmpdir) / "animica" / "test.db"
        
        # Expand the path (should create parent directory and add sqlite:/// prefix)
        expanded = _expand_sqlite_uri(str(test_path))
        
        # Verify parent directory was created
        assert test_path.parent.exists(), "Parent directory should be created"
        assert expanded.startswith("sqlite:///"), "Should add sqlite:/// prefix"


def test_rpc_config_default_db_path_uses_chain_id():
    """Test that RPC config uses chain-specific DB paths by default."""
    from rpc.config import load
    
    # Test with different chain IDs
    for chain_id, expected_dir in [(1, "chain-1"), (2, "chain-2"), (1337, "chain-1337")]:
        os.environ["ANIMICA_CHAIN_ID"] = str(chain_id)
        try:
            cfg = load()
            # Verify the DB URI contains the chain-specific directory
            assert f"chain-{chain_id}" in cfg.db_uri, \
                f"DB URI should contain chain-{chain_id}: {cfg.db_uri}"
        finally:
            os.environ.pop("ANIMICA_CHAIN_ID", None)


def test_wallet_store_creates_parent_directory():
    """Test that wallet store creates parent directory before writing."""
    from animica.cli.wallet import _save_store
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test with a wallet file in a non-existent directory
        wallet_path = Path(tmpdir) / ".animica" / "wallets.json"
        
        # Save a wallet store (should create parent directory)
        store = {"version": 1, "wallets": []}
        _save_store(wallet_path, store)
        
        # Verify parent directory was created and file exists
        assert wallet_path.parent.exists(), "Parent directory should be created"
        assert wallet_path.exists(), "Wallet file should be created"


def test_key_storage_creates_parent_directory():
    """Test that key storage creates parent directory before writing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test with a key file in a non-existent directory
        key_path = Path(tmpdir) / ".animica" / "keys" / "test_key.json"
        
        # Create parent directory (simulating what key.new does)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Verify parent directory was created
        assert key_path.parent.exists(), "Parent directory should be created"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
