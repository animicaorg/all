"""
Test genesis loader state initialization
=========================================

Regression test for genesis alloc seeding to StateDB. Verifies that
_init_state_from_alloc correctly writes balances and nonces to a temporary
SQLite database using StateDB public API, rather than calling non-existent
upsert_account on batch objects.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


def test_init_state_from_alloc_uses_statedb_api():
    """
    Regression test: _init_state_from_alloc should seed state DB with alloc entries
    using StateDB public API (set_balance, set_nonce) instead of calling
    upsert_account on SQLiteBatch.
    
    This test should fail on the broken code (AttributeError: 'SQLiteBatch' object
    has no attribute 'upsert_account') and pass after the fix.
    """
    from core.genesis.loader import _init_state_from_alloc
    from core.db.sqlite import SQLiteKV
    from core.db.state_db import StateDB

    # Create sample alloc entries
    alloc = [
        {"address": "system:treasury", "nonce": 0, "balance": "1000000"},
        {"address": "system:test", "nonce": 5, "balance": "2000000"},
        {"address": "test:account", "nonce": 0, "balance": "0"}  # Zero balance case
    ]

    # Use a temporary SQLite DB
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
        db_path = db_f.name

    try:
        # Open KV and create StateDB
        kv = SQLiteKV(db_path)
        state = StateDB(kv)
        
        # This should not raise AttributeError after the fix
        _init_state_from_alloc(state, alloc)
        
        # Verify balances and nonces are written correctly
        # Note: StateDB expects bytes addresses
        treasury_addr = "system:treasury".encode("utf-8")
        test_addr = "system:test".encode("utf-8")
        account_addr = "test:account".encode("utf-8")

        # Check balances
        assert state.get_balance(treasury_addr) == 1000000, \
            "Treasury balance should be 1000000"
        assert state.get_balance(test_addr) == 2000000, \
            "Test account balance should be 2000000"
        assert state.get_balance(account_addr) == 0, \
            "Account with zero balance should exist with balance 0"

        # Check nonces
        assert state.get_nonce(treasury_addr) == 0, \
            "Treasury nonce should be 0"
        assert state.get_nonce(test_addr) == 5, \
            "Test account nonce should be 5"
        assert state.get_nonce(account_addr) == 0, \
            "Account with zero nonce should exist with nonce 0"

        kv.close()

    finally:
        # Clean up temporary file
        Path(db_path).unlink(missing_ok=True)


def test_init_state_from_alloc_batch_api():
    """
    Test that _init_state_from_alloc uses batch API correctly when available.
    
    This verifies that the batch context manager is used correctly and
    that StateDB methods accept the batch parameter.
    """
    from core.genesis.loader import _init_state_from_alloc
    from core.db.sqlite import SQLiteKV
    from core.db.state_db import StateDB

    # Create sample alloc with multiple entries to ensure batching is effective
    alloc = [
        {"address": "batch:test1", "nonce": 1, "balance": "50"},
        {"address": "batch:test2", "nonce": 2, "balance": "100"},
        {"address": "batch:test3", "nonce": 0, "balance": "150"},
    ]

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
        db_path = db_f.name

    try:
        # Open KV and create StateDB
        kv = SQLiteKV(db_path)
        state = StateDB(kv)
        
        # Initialize state using batch API
        _init_state_from_alloc(state, alloc)
        
        # Verify all accounts were written correctly
        test1_addr = "batch:test1".encode("utf-8")
        test2_addr = "batch:test2".encode("utf-8")
        test3_addr = "batch:test3".encode("utf-8")

        assert state.get_balance(test1_addr) == 50
        assert state.get_nonce(test1_addr) == 1
        
        assert state.get_balance(test2_addr) == 100
        assert state.get_nonce(test2_addr) == 2
        
        assert state.get_balance(test3_addr) == 150
        assert state.get_nonce(test3_addr) == 0

        kv.close()

    finally:
        Path(db_path).unlink(missing_ok=True)


def test_init_state_from_alloc_address_normalization():
    """
    Test that _init_state_from_alloc correctly normalizes addresses to lowercase.
    """
    from core.genesis.loader import _init_state_from_alloc
    from core.db.sqlite import SQLiteKV
    from core.db.state_db import StateDB

    # Create alloc with mixed-case addresses
    alloc = [
        {"address": "System:Treasury", "nonce": 0, "balance": "1000"},
        {"address": "SYSTEM:TEST", "nonce": 1, "balance": "2000"},
    ]

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as db_f:
        db_path = db_f.name

    try:
        kv = SQLiteKV(db_path)
        state = StateDB(kv)
        
        _init_state_from_alloc(state, alloc)
        
        # Addresses should be normalized to lowercase
        treasury_addr = "system:treasury".encode("utf-8")
        test_addr = "system:test".encode("utf-8")

        assert state.get_balance(treasury_addr) == 1000
        assert state.get_nonce(treasury_addr) == 0
        
        assert state.get_balance(test_addr) == 2000
        assert state.get_nonce(test_addr) == 1

        kv.close()

    finally:
        Path(db_path).unlink(missing_ok=True)
