"""
Test genesis initialization regression
=======================================

Regression tests for load_and_init_genesis to ensure:
1. State is seeded correctly with alloc entries (balances and nonces)
2. Genesis header is stored in BlockDB
3. Canonical head is set to height 0 with correct hash

These tests verify the fix for the bug where:
- put_header was called with incorrect signature: put_header(0, header) instead of put_header(header)
- Canonical head was not properly established with set_canonical and set_head
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


def test_load_and_init_genesis_full_flow():
    """
    End-to-end test: load_and_init_genesis with a temp SQLite DB should:
    - Seed alloc balances/nonces to state
    - Store genesis header
    - Set canonical head to height 0
    
    This is the primary regression test for the genesis init bug fix.
    """
    from core.genesis.loader import load_and_init_genesis
    
    # Create a minimal genesis file with multiple accounts
    genesis_data = {
        "chainId": 1337,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [
            {"address": "system:treasury", "nonce": 0, "balance": 1000000},
            {"address": "system:validator", "nonce": 5, "balance": 2000000},
            {"address": "system:user", "nonce": 0, "balance": 500000}
        ],
        "economics": {"premineTotal": 5000000},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_data, gf)
        genesis_path = gf.name
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = dbf.name
    
    try:
        db_uri = f"sqlite:///{db_path}"
        env = load_and_init_genesis(genesis_path, db_uri, log=False)
        
        # Test 1: Verify state was seeded with correct balances and nonces
        state = env["state"]
        
        treasury_addr = "system:treasury".encode("utf-8")
        validator_addr = "system:validator".encode("utf-8")
        user_addr = "system:user".encode("utf-8")
        
        assert state.get_balance(treasury_addr) == 1000000, \
            "Treasury balance should be 1000000"
        assert state.get_nonce(treasury_addr) == 0, \
            "Treasury nonce should be 0"
        
        assert state.get_balance(validator_addr) == 2000000, \
            "Validator balance should be 2000000"
        assert state.get_nonce(validator_addr) == 5, \
            "Validator nonce should be 5"
        
        assert state.get_balance(user_addr) == 500000, \
            "User balance should be 500000"
        assert state.get_nonce(user_addr) == 0, \
            "User nonce should be 0"
        
        # Test 2: Verify canonical head is set to height 0
        blocks = env["blocks"]
        head = blocks.get_head()
        
        assert head is not None, "Canonical head should be set"
        head_height, head_hash = head
        assert head_height == 0, "Head height should be 0"
        assert len(head_hash) == 32, "Head hash should be 32 bytes"
        
        # Test 3: Verify genesis header is stored and retrievable
        header = blocks.get_header_by_height(0)
        assert header is not None, "Genesis header should be stored at height 0"
        assert header.chainId == 1337, "Chain ID should match genesis"
        
        # Test 4: Verify hash consistency
        assert env["head_hash"] == head_hash, \
            "Returned head_hash should match BlockDB head"
        assert env["head_height"] == 0, \
            "Returned head_height should be 0"
        
        # Test 5: Verify canonical index
        canonical_hash = blocks.get_canonical_hash(0)
        assert canonical_hash is not None, "Canonical hash should be set for height 0"
        assert canonical_hash == head_hash, \
            "Canonical hash should match head hash"
        
        # Clean up
        env["kv"].close()
        
    finally:
        Path(genesis_path).unlink(missing_ok=True)
        Path(db_path).unlink(missing_ok=True)


def test_load_and_init_genesis_empty_alloc():
    """
    Test genesis init with empty alloc list (no premine).
    Should still create a valid genesis header and canonical head.
    """
    from core.genesis.loader import load_and_init_genesis
    
    genesis_data = {
        "chainId": 9999,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [],  # Empty alloc
        "economics": {"premineTotal": 0},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_data, gf)
        genesis_path = gf.name
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = dbf.name
    
    try:
        db_uri = f"sqlite:///{db_path}"
        env = load_and_init_genesis(genesis_path, db_uri, log=False)
        
        # Verify head is still set
        blocks = env["blocks"]
        head = blocks.get_head()
        assert head is not None, "Head should be set even with empty alloc"
        assert head[0] == 0, "Head height should be 0"
        
        # Verify header is stored
        header = blocks.get_header_by_height(0)
        assert header is not None, "Genesis header should be stored"
        
        env["kv"].close()
        
    finally:
        Path(genesis_path).unlink(missing_ok=True)
        Path(db_path).unlink(missing_ok=True)


def test_load_and_init_genesis_state_root_consistency():
    """
    Test that the state root in the genesis header matches the computed
    state root from the alloc.
    """
    from core.genesis.loader import load_and_init_genesis, compute_state_root_from_alloc
    
    alloc = [
        {"address": "system:addr1", "nonce": 0, "balance": 12345},
        {"address": "system:addr2", "nonce": 10, "balance": 67890}
    ]
    
    genesis_data = {
        "chainId": 7777,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": alloc,
        "economics": {"premineTotal": 100000},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    # Compute expected state root
    expected_state_root = compute_state_root_from_alloc(alloc)
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_data, gf)
        genesis_path = gf.name
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = dbf.name
    
    try:
        db_uri = f"sqlite:///{db_path}"
        env = load_and_init_genesis(genesis_path, db_uri, log=False)
        
        # Verify state root matches
        assert env["state_root"] == expected_state_root, \
            "State root should match computed value"
        
        # Verify header contains the same state root
        header = env["genesis_header"]
        assert header.stateRoot == expected_state_root, \
            "Header state root should match computed value"
        
        env["kv"].close()
        
    finally:
        Path(genesis_path).unlink(missing_ok=True)
        Path(db_path).unlink(missing_ok=True)


def test_load_and_init_genesis_address_normalization():
    """
    Test that addresses in alloc are normalized (lowercased) when seeded to state.
    """
    from core.genesis.loader import load_and_init_genesis
    
    genesis_data = {
        "chainId": 5555,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [
            {"address": "System:Treasury", "nonce": 0, "balance": 1000},
            {"address": "SYSTEM:USER", "nonce": 1, "balance": 2000}
        ],
        "economics": {"premineTotal": 5000},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_data, gf)
        genesis_path = gf.name
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = dbf.name
    
    try:
        db_uri = f"sqlite:///{db_path}"
        env = load_and_init_genesis(genesis_path, db_uri, log=False)
        
        state = env["state"]
        
        # Addresses should be normalized to lowercase
        treasury_addr = "system:treasury".encode("utf-8")
        user_addr = "system:user".encode("utf-8")
        
        assert state.get_balance(treasury_addr) == 1000, \
            "Treasury balance should be accessible with lowercase address"
        assert state.get_nonce(treasury_addr) == 0
        
        assert state.get_balance(user_addr) == 2000, \
            "User balance should be accessible with lowercase address"
        assert state.get_nonce(user_addr) == 1
        
        env["kv"].close()
        
    finally:
        Path(genesis_path).unlink(missing_ok=True)
        Path(db_path).unlink(missing_ok=True)


def test_load_and_init_genesis_multiple_calls():
    """
    Test that calling load_and_init_genesis multiple times on the same DB
    is idempotent (or at least doesn't crash).
    """
    from core.genesis.loader import load_and_init_genesis
    
    genesis_data = {
        "chainId": 3333,
        "genesisTime": "2024-01-01T00:00:00Z",
        "alloc": [{"address": "system:single", "nonce": 0, "balance": 999}],
        "economics": {"premineTotal": 1000},
        "consensus": {"initialThetaMicro": 1000000}
    }
    
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as gf:
        json.dump(genesis_data, gf)
        genesis_path = gf.name
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as dbf:
        db_path = dbf.name
    
    try:
        db_uri = f"sqlite:///{db_path}"
        
        # First call
        env1 = load_and_init_genesis(genesis_path, db_uri, log=False)
        head1 = env1["blocks"].get_head()
        env1["kv"].close()
        
        # Second call on same DB
        env2 = load_and_init_genesis(genesis_path, db_uri, log=False)
        head2 = env2["blocks"].get_head()
        
        # Should produce the same result
        assert head1 == head2, "Multiple inits should produce same head"
        
        env2["kv"].close()
        
    finally:
        Path(genesis_path).unlink(missing_ok=True)
        Path(db_path).unlink(missing_ok=True)
