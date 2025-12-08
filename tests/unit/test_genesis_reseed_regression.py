"""
Test genesis reseeding and RPC server integration
=================================================

Regression tests for the specific issue where:
1. Running load_and_init_genesis with a genesis file and DB URI writes alloc balances
2. Starting rpc.server on that DB should NOT reinitialize or overwrite state
3. Both direct StateDB reads and RPC state.getBalance should return the correct balances
4. Absolute sqlite URIs like sqlite:////root/animica/data/mainnet.db should work

This tests the fixes for:
- StateDB.ensure_account properly passing batch parameter
- rpc.deps._maybe_bootstrap_genesis not opening DB twice
- Genesis loader handling absolute paths correctly
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


def test_reseed_existing_db_preserves_balances():
    """
    Test that calling load_and_init_genesis twice on the same DB file
    correctly updates/preserves balances (idempotent behavior).
    
    This is the core regression test for the reseeding issue.
    """
    from core.genesis.loader import load_and_init_genesis
    
    # Create a genesis with specific balances
    genesis_v1 = {
        "chainId": 1,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [
            {"address": "system:treasury", "nonce": 0, "balance": "1000000"},
            {"address": "system:user1", "nonce": 0, "balance": "500000"}
        ],
        "economics": {"premineTotal": 2000000},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_v1, gf)
        genesis_path = gf.name
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = dbf.name
    
    try:
        db_uri = f"sqlite:///{db_path}"
        
        # First seeding
        env1 = load_and_init_genesis(genesis_path, db_uri, override_chain_id=1, log=False)
        
        # Verify balances after first seeding
        state1 = env1["state"]
        treasury_addr = "system:treasury".encode("utf-8")
        user1_addr = "system:user1".encode("utf-8")
        
        assert state1.get_balance(treasury_addr) == 1000000, \
            "Treasury balance should be 1000000 after first seeding"
        assert state1.get_balance(user1_addr) == 500000, \
            "User1 balance should be 500000 after first seeding"
        
        env1["kv"].close()
        
        # Second seeding on same DB (reseeding scenario)
        env2 = load_and_init_genesis(genesis_path, db_uri, override_chain_id=1, log=False)
        
        # Verify balances are still correct after reseeding
        state2 = env2["state"]
        
        assert state2.get_balance(treasury_addr) == 1000000, \
            "Treasury balance should still be 1000000 after reseeding"
        assert state2.get_balance(user1_addr) == 500000, \
            "User1 balance should still be 500000 after reseeding"
        
        env2["kv"].close()
        
    finally:
        Path(genesis_path).unlink(missing_ok=True)
        Path(db_path).unlink(missing_ok=True)


def test_reseed_with_updated_genesis():
    """
    Test that reseeding with a different genesis file updates the balances.
    This verifies that load_and_init_genesis is truly idempotent and updates state.
    """
    from core.genesis.loader import load_and_init_genesis
    
    genesis_v1 = {
        "chainId": 1,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [
            {"address": "system:account", "nonce": 0, "balance": "100"}
        ],
        "economics": {"premineTotal": 100},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    genesis_v2 = {
        "chainId": 1,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [
            {"address": "system:account", "nonce": 0, "balance": "200"}  # Updated balance
        ],
        "economics": {"premineTotal": 200},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_v1, gf)
        genesis_path_v1 = gf.name
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_v2, gf)
        genesis_path_v2 = gf.name
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = dbf.name
    
    try:
        db_uri = f"sqlite:///{db_path}"
        account_addr = "system:account".encode("utf-8")
        
        # Seed with v1
        env1 = load_and_init_genesis(genesis_path_v1, db_uri, override_chain_id=1, log=False)
        assert env1["state"].get_balance(account_addr) == 100
        env1["kv"].close()
        
        # Reseed with v2 (different balance)
        env2 = load_and_init_genesis(genesis_path_v2, db_uri, override_chain_id=1, log=False)
        assert env2["state"].get_balance(account_addr) == 200, \
            "Balance should be updated to 200 after reseeding with v2"
        env2["kv"].close()
        
    finally:
        Path(genesis_path_v1).unlink(missing_ok=True)
        Path(genesis_path_v2).unlink(missing_ok=True)
        Path(db_path).unlink(missing_ok=True)


def test_absolute_sqlite_uri_with_four_slashes():
    """
    Test that absolute sqlite URIs with 4 slashes work correctly.
    E.g., sqlite:////root/animica/data/mainnet.db -> /root/animica/data/mainnet.db
    """
    from core.genesis.loader import load_and_init_genesis
    
    genesis_data = {
        "chainId": 1,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [
            {"address": "system:absolute", "nonce": 0, "balance": "12345"}
        ],
        "economics": {"premineTotal": 12345},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_data, gf)
        genesis_path = gf.name
    
    # Create a temp directory to simulate absolute path
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test.db")
    
    try:
        # Use absolute path URI with 4 slashes
        db_uri = f"sqlite:///{db_path}"
        
        env = load_and_init_genesis(genesis_path, db_uri, override_chain_id=1, log=False)
        
        # Verify the DB file was created at the absolute path
        assert os.path.exists(db_path), f"DB file should exist at {db_path}"
        
        # Verify balance was written
        state = env["state"]
        test_addr = "system:absolute".encode("utf-8")
        assert state.get_balance(test_addr) == 12345
        
        env["kv"].close()
        
    finally:
        Path(genesis_path).unlink(missing_ok=True)
        if os.path.exists(db_path):
            os.unlink(db_path)
        # Clean up any SQLite WAL files
        import glob
        for wal_file in glob.glob(os.path.join(temp_dir, "*.db-*")):
            try:
                os.unlink(wal_file)
            except OSError:
                pass  # WAL file might be locked or already deleted
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass  # Directory not empty, that's okay


def test_statedb_direct_read_after_seeding():
    """
    Test that StateDB can be opened separately after seeding and still reads
    the correct balances. This simulates the user's reproduction script.
    """
    from core.genesis.loader import load_and_init_genesis
    from core.db import open_kv
    from core.db.state_db import StateDB
    
    genesis_data = {
        "chainId": 1,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [
            {"address": "system:direct", "nonce": 7, "balance": "81000000000000000"}
        ],
        "economics": {"premineTotal": "81000000000000000"},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_data, gf)
        genesis_path = gf.name
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = dbf.name
    
    try:
        db_uri = f"sqlite:///{db_path}"
        
        # Seed the DB
        env = load_and_init_genesis(genesis_path, db_uri, override_chain_id=1, log=False)
        env["kv"].close()
        
        # Now open a fresh KV and StateDB (simulates separate process)
        kv = open_kv(db_uri)
        state = StateDB(kv)
        
        # Verify balance can be read
        test_addr = "system:direct".encode("utf-8")
        balance = state.get_balance(test_addr)
        nonce = state.get_nonce(test_addr)
        
        assert balance == 81000000000000000, \
            f"Balance should be 81000000000000000, got {balance}"
        assert nonce == 7, \
            f"Nonce should be 7, got {nonce}"
        
        kv.close()
        
    finally:
        Path(genesis_path).unlink(missing_ok=True)
        Path(db_path).unlink(missing_ok=True)


def test_batch_write_commits_properly():
    """
    Test that batch writes in _init_state_from_alloc commit properly
    and data is persisted after the batch context exits.
    
    This specifically tests the fix for ensure_account not passing batch parameter.
    """
    from core.genesis.loader import _init_state_from_alloc
    from core.db.sqlite import SQLiteKV
    from core.db.state_db import StateDB
    
    alloc = [
        {"address": "system:batch1", "nonce": 0, "balance": "999"},
        {"address": "system:batch2", "nonce": 5, "balance": "888"},
    ]
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
        db_path = db_f.name
    
    try:
        # Initialize state with batch
        kv = SQLiteKV(db_path)
        state = StateDB(kv)
        _init_state_from_alloc(state, alloc)
        kv.close()
        
        # Re-open DB and verify data persisted
        kv2 = SQLiteKV(db_path)
        state2 = StateDB(kv2)
        
        addr1 = "system:batch1".encode("utf-8")
        addr2 = "system:batch2".encode("utf-8")
        
        assert state2.get_balance(addr1) == 999, \
            "Batch write for addr1 should have persisted"
        assert state2.get_nonce(addr1) == 0
        
        assert state2.get_balance(addr2) == 888, \
            "Batch write for addr2 should have persisted"
        assert state2.get_nonce(addr2) == 5
        
        kv2.close()
        
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_rpc_deps_does_not_double_open_db():
    """
    Test that rpc.deps.build_context does not open the DB twice when
    bootstrapping genesis. This test verifies the fix for the double-open issue.
    
    Note: This test checks that build_context with an empty DB calls load_genesis
    with the existing KV, not load_and_init_genesis with a new URI.
    """
    import logging
    from unittest.mock import patch, MagicMock
    
    # Create a minimal test DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = dbf.name
    
    # Create a minimal genesis file
    genesis_data = {
        "chainId": 1337,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [{"address": "system:mock", "nonce": 0, "balance": "100"}],
        "economics": {"premineTotal": 100},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_data, gf)
        genesis_path = gf.name
    
    try:
        db_uri = f"sqlite:///{db_path}"
        
        # Mock config to use our test DB and genesis
        mock_cfg = MagicMock()
        mock_cfg.db_uri = db_uri
        mock_cfg.chain_id = 1337
        mock_cfg.genesis_path = Path(genesis_path)
        mock_cfg.log_level = "INFO"
        
        # Track how many times _open_kv is called
        open_kv_calls = []
        
        from rpc import deps
        original_open_kv = deps._open_kv
        
        def tracked_open_kv(uri):
            open_kv_calls.append(uri)
            return original_open_kv(uri)
        
        with patch.object(deps, '_open_kv', side_effect=tracked_open_kv):
            ctx = deps.build_context(mock_cfg)
            
            # Verify _open_kv was called exactly once (not twice)
            assert len(open_kv_calls) == 1, \
                f"_open_kv should be called once, was called {len(open_kv_calls)} times"
            assert open_kv_calls[0] == db_uri
            
            # Verify context was built successfully
            assert ctx is not None
            assert ctx.kv is not None
            assert ctx.state_db is not None
            
            ctx.close()
        
    finally:
        Path(genesis_path).unlink(missing_ok=True)
        Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
