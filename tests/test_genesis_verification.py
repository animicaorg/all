"""
Test genesis verification and network identity enforcement.

These tests validate that:
1. Genesis verification CLI works correctly
2. DB metadata is checked on startup
3. Mismatched genesis/network causes clear errors
"""

import os
import tempfile
from pathlib import Path

import pytest

from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV
from core.network_manifest import (
    MAINNET_MANIFEST,
    TESTNET_MANIFEST,
    get_manifest,
    verify_genesis,
)


def test_get_manifest_by_network():
    """Test that we can retrieve manifests by network name."""
    manifest = get_manifest(network="mainnet")
    assert manifest is not None
    assert manifest.network_name == "mainnet"
    assert manifest.chain_id == 0
    assert manifest.p2p_network_id == "animica:0"

    manifest = get_manifest(network="testnet")
    assert manifest is not None
    assert manifest.network_name == "testnet"
    assert manifest.chain_id == 2

    manifest = get_manifest(network="devnet")
    assert manifest is not None
    assert manifest.network_name == "devnet"
    assert manifest.chain_id == 1337


def test_get_manifest_by_chain_id():
    """Test that we can retrieve manifests by chain_id."""
    manifest = get_manifest(chain_id=0)
    assert manifest is not None
    assert manifest.network_name == "mainnet"
    assert manifest.chain_id == 0

    manifest = get_manifest(chain_id=2)
    assert manifest is not None
    assert manifest.network_name == "testnet"

    manifest = get_manifest(chain_id=1337)
    assert manifest is not None
    assert manifest.network_name == "devnet"


def test_verify_genesis_mainnet():
    """Test that mainnet genesis verification succeeds."""
    manifest = MAINNET_MANIFEST
    # This should succeed without raising
    is_valid = verify_genesis(manifest, raise_on_mismatch=False)
    assert is_valid is True


def test_verify_genesis_testnet():
    """Test that testnet genesis verification succeeds."""
    manifest = TESTNET_MANIFEST
    # This should succeed without raising
    is_valid = verify_genesis(manifest, raise_on_mismatch=False)
    assert is_valid is True


def test_network_identity_string():
    """Test that network identity string is formatted correctly."""
    manifest = MAINNET_MANIFEST
    identity = manifest.network_identity_string
    
    # Should contain network name, chain_id, and genesis hash prefix
    assert "mainnet" in identity
    assert "chain_0" in identity
    assert "genesis_" in identity
    assert len(identity.split(":")) == 3


def test_db_metadata_storage():
    """Test that we can store and retrieve network metadata from DB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        kv = SQLiteKV(str(db_path))
        block_db = BlockDB(kv)
        
        # Store metadata
        block_db.set_chain_id(0)
        block_db.set_network_name("mainnet")
        genesis_hash = MAINNET_MANIFEST.pinned_genesis_hash
        block_db.set_genesis_hash(genesis_hash)
        
        # Retrieve and verify
        assert block_db.get_chain_id() == 0
        assert block_db.get_network_name() == "mainnet"
        assert block_db.get_genesis_hash() == genesis_hash
        
        kv.close()


def test_db_metadata_chain_id_mismatch():
    """Test that chain_id mismatch is detected."""
    from rpc.deps import _DbBundle, _maybe_bootstrap_genesis
    from core.errors import GenesisMismatchError
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        kv = SQLiteKV(str(db_path))
        block_db = BlockDB(kv)
        
        # Store metadata for mainnet (chain_id=0)
        block_db.set_chain_id(0)
        block_db.set_network_name("mainnet")
        
        # Try to bootstrap with testnet (chain_id=2)
        bundle = _DbBundle(
            kv=kv,
            state_db=None,
            block_db=block_db,
            tx_index=None,
        )
        
        with pytest.raises(GenesisMismatchError) as exc_info:
            _maybe_bootstrap_genesis(
                bundle=bundle,
                chain_id=2,  # Testnet chain_id
                genesis_path=TESTNET_MANIFEST.genesis_path,
            )
        
        assert "chain_id mismatch" in str(exc_info.value).lower()
        assert "chain_id=0" in str(exc_info.value)
        assert "chain_id=2" in str(exc_info.value)
        
        kv.close()


def test_db_metadata_genesis_hash_mismatch():
    """Test that genesis hash mismatch is detected."""
    from rpc.deps import _DbBundle, _maybe_bootstrap_genesis
    from core.errors import GenesisMismatchError
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        kv = SQLiteKV(str(db_path))
        block_db = BlockDB(kv)
        
        # Store metadata for mainnet
        block_db.set_chain_id(0)
        block_db.set_genesis_hash(b'\x00' * 32)  # Wrong genesis hash
        
        # Try to bootstrap with mainnet (should detect hash mismatch)
        bundle = _DbBundle(
            kv=kv,
            state_db=None,
            block_db=block_db,
            tx_index=None,
        )
        
        with pytest.raises(GenesisMismatchError) as exc_info:
            _maybe_bootstrap_genesis(
                bundle=bundle,
                chain_id=0,
                genesis_path=MAINNET_MANIFEST.genesis_path,
            )
        
        assert "genesis mismatch" in str(exc_info.value).lower()
        
        kv.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
