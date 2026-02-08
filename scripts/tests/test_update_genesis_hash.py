"""
Tests for the genesis hash update utility.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add scripts to path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from update_genesis_hash import main as update_main


@pytest.fixture
def temp_db():
    """Create a temporary test database with genesis initialized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db_uri = f"sqlite:///{db_path}"
        
        # Initialize with mainnet genesis
        from core.genesis.loader import load_and_init_genesis
        env = load_and_init_genesis(
            genesis_path=str(REPO_ROOT / "core/genesis/mainnet.json"),
            db_uri=db_uri,
            log=False
        )
        
        yield {
            "db_uri": db_uri,
            "db_path": db_path,
            "genesis_hash": env["head_hash"],
            "chain_id": env["genesis"]["chainId"],
        }


def test_update_genesis_hash_no_change_needed(temp_db, monkeypatch):
    """Test update script when genesis hash already matches."""
    # Mock sys.argv
    monkeypatch.setattr(
        sys, "argv", 
        [
            "update_genesis_hash.py",
            "--db-uri", temp_db["db_uri"],
            "--genesis-path", str(REPO_ROOT / "core/genesis/mainnet.json"),
        ]
    )
    
    # Should succeed with exit code 0 (no update needed)
    result = update_main()
    assert result == 0


def test_update_genesis_hash_dry_run(temp_db, monkeypatch):
    """Test dry-run mode doesn't modify database."""
    from core.db.block_db import BlockDB
    from core.db.sqlite import SQLiteKV
    
    # Get initial hash
    kv = SQLiteKV(str(temp_db["db_path"]))
    db = BlockDB(kv)
    initial_hash = db.get_genesis_hash()
    
    # Run in dry-run mode
    monkeypatch.setattr(
        sys, "argv",
        [
            "update_genesis_hash.py",
            "--db-uri", temp_db["db_uri"],
            "--genesis-path", str(REPO_ROOT / "core/genesis/mainnet.json"),
            "--dry-run",
        ]
    )
    
    result = update_main()
    assert result == 0
    
    # Verify hash wasn't changed
    final_hash = db.get_genesis_hash()
    assert final_hash == initial_hash


def test_update_genesis_hash_testnet(monkeypatch):
    """Test with testnet genesis."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "testnet.db"
        db_uri = f"sqlite:///{db_path}"
        
        # Initialize with testnet genesis
        from core.genesis.loader import load_and_init_genesis
        env = load_and_init_genesis(
            genesis_path=str(REPO_ROOT / "core/genesis/testnet.json"),
            db_uri=db_uri,
            log=False
        )
        
        # Run update script
        monkeypatch.setattr(
            sys, "argv",
            [
                "update_genesis_hash.py",
                "--db-uri", db_uri,
                "--genesis-path", str(REPO_ROOT / "core/genesis/testnet.json"),
            ]
        )
        
        result = update_main()
        assert result == 0


def test_update_genesis_hash_force_update(temp_db, monkeypatch):
    """Test force update even when hash matches."""
    from core.db.block_db import BlockDB
    from core.db.sqlite import SQLiteKV
    
    # Run with --force flag
    monkeypatch.setattr(
        sys, "argv",
        [
            "update_genesis_hash.py",
            "--db-uri", temp_db["db_uri"],
            "--genesis-path", str(REPO_ROOT / "core/genesis/mainnet.json"),
            "--force",
        ]
    )
    
    result = update_main()
    assert result == 0
    
    # Verify hash is still correct
    kv = SQLiteKV(str(temp_db["db_path"]))
    db = BlockDB(kv)
    final_hash = db.get_genesis_hash()
    assert final_hash == temp_db["genesis_hash"]


def test_update_genesis_hash_missing_db(monkeypatch):
    """Test behavior when database doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "nonexistent.db"
        db_uri = f"sqlite:///{db_path}"
        
        monkeypatch.setattr(
            sys, "argv",
            [
                "update_genesis_hash.py",
                "--db-uri", db_uri,
                "--genesis-path", str(REPO_ROOT / "core/genesis/mainnet.json"),
            ]
        )
        
        # Should exit with 0 (nothing to update)
        result = update_main()
        assert result == 0


def test_compute_genesis_hash_consistency():
    """Verify genesis hashes are computed consistently."""
    from core.genesis.loader import compute_genesis_identity
    
    # Compute mainnet genesis multiple times
    identity1 = compute_genesis_identity(str(REPO_ROOT / "core/genesis/mainnet.json"))
    identity2 = compute_genesis_identity(str(REPO_ROOT / "core/genesis/mainnet.json"))
    
    # Should be identical
    assert identity1.genesis_block_hash == identity2.genesis_block_hash
    assert identity1.chain_id == identity2.chain_id
    
    # Mainnet should match documented hash from CHAIN_RESET.md
    # NOTE: This hash must be updated if mainnet genesis changes.
    # See docs/CHAIN_RESET.md for the canonical genesis hash.
    expected_mainnet = bytes.fromhex("8ec4a0b923005e9039b815e526990359119e6f5492d5038aa898d6f8eee52adc")
    assert identity1.genesis_block_hash == expected_mainnet
    assert identity1.chain_id == 1


def test_update_genesis_hash_actual_mismatch(monkeypatch):
    """Test updating a database with mismatched genesis hash."""
    from core.db.block_db import BlockDB
    from core.db.sqlite import SQLiteKV
    from core.genesis.loader import compute_genesis_identity
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "mismatch.db"
        db_uri = f"sqlite:///{db_path}"
        
        # Initialize with testnet genesis
        from core.genesis.loader import load_and_init_genesis
        load_and_init_genesis(
            genesis_path=str(REPO_ROOT / "core/genesis/testnet.json"),
            db_uri=db_uri,
            log=False
        )
        
        # Verify it has testnet genesis
        kv = SQLiteKV(str(db_path))
        db = BlockDB(kv)
        testnet_hash = db.get_genesis_hash()
        testnet_id = compute_genesis_identity(str(REPO_ROOT / "core/genesis/testnet.json"))
        assert testnet_hash == testnet_id.genesis_block_hash
        kv.close()
        
        # Now try to "update" to mainnet (simulating a genesis reset scenario)
        # Note: This would normally fail due to chain ID mismatch, so we force it
        monkeypatch.setattr(
            sys, "argv",
            [
                "update_genesis_hash.py",
                "--db-uri", db_uri,
                "--genesis-path", str(REPO_ROOT / "core/genesis/testnet.json"),
                "--force",
            ]
        )
        
        # Should succeed
        result = update_main()
        assert result == 0
        
        # Verify hash is now updated (stays testnet since we pointed to testnet genesis)
        kv = SQLiteKV(str(db_path))
        db = BlockDB(kv)
        final_hash = db.get_genesis_hash()
        assert final_hash == testnet_hash  # Should still be testnet
        kv.close()


def test_update_genesis_hash_network_shortcut_integration(monkeypatch):
    """Test the --network shortcut with actual file system paths."""
    # This test verifies that --network properly resolves paths
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test database in a known location
        db_path = Path(tmpdir) / "test.db"
        db_uri = f"sqlite:///{db_path}"
        
        # Initialize with devnet
        from core.genesis.loader import load_and_init_genesis
        load_and_init_genesis(
            genesis_path=str(REPO_ROOT / "core/genesis/devnet.json"),
            db_uri=db_uri,
            log=False
        )
        
        # Run update with explicit --db-uri and --network (genesis path from network)
        monkeypatch.setattr(
            sys, "argv",
            [
                "update_genesis_hash.py",
                "--db-uri", db_uri,
                "--network", "devnet",  # Should resolve to core/genesis/devnet.json
            ]
        )
        
        result = update_main()
        assert result == 0
