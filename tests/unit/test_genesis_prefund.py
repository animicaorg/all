"""
Test genesis allocations for pre-funded user address
====================================================

Verifies that the user address is pre-funded with 500M ANM in devnet and testnet genesis files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _load_genesis(filename: str) -> dict:
    """Load a genesis file from the genesis directory."""
    repo_root = Path(__file__).resolve().parents[2]
    genesis_path = repo_root / "genesis" / filename
    with genesis_path.open("r") as f:
        return json.load(f)


def test_devnet_genesis_has_prefund():
    """Test that devnet genesis has the pre-funded user address."""
    genesis = _load_genesis("genesis.sample.devnet.json")
    
    # Check it's devnet
    assert genesis["chainId"] == 1337
    
    # Find the user address in allocations
    user_address = "anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9"
    alloc = genesis.get("alloc", [])
    
    user_alloc = None
    for entry in alloc:
        if entry.get("address") == user_address:
            user_alloc = entry
            break
    
    assert user_alloc is not None, f"User address {user_address} not found in devnet genesis"
    
    # Check the balance (500M ANM = 500000000000000000 base units)
    balance = int(user_alloc["balance"])
    expected_balance = 500_000_000_000_000_000
    assert balance == expected_balance, f"Expected balance {expected_balance}, got {balance}"
    
    # Check nonce is 0
    assert user_alloc["nonce"] == 0


def test_testnet_genesis_has_prefund():
    """Test that testnet genesis has the pre-funded user address."""
    genesis = _load_genesis("genesis.sample.testnet.json")
    
    # Check it's testnet
    assert genesis["chainId"] == 2
    
    # Find the user address in allocations
    user_address = "anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9"
    alloc = genesis.get("alloc", [])
    
    user_alloc = None
    for entry in alloc:
        if entry.get("address") == user_address:
            user_alloc = entry
            break
    
    assert user_alloc is not None, f"User address {user_address} not found in testnet genesis"
    
    # Check the balance (500M ANM = 500000000000000000 base units)
    balance = int(user_alloc["balance"])
    expected_balance = 500_000_000_000_000_000
    assert balance == expected_balance, f"Expected balance {expected_balance}, got {balance}"
    
    # Check nonce is 0
    assert user_alloc["nonce"] == 0


def test_mainnet_genesis_no_prefund():
    """Test that mainnet genesis does NOT have the pre-funded user address."""
    genesis = _load_genesis("genesis.sample.mainnet.json")
    
    # Check it's mainnet
    assert genesis["chainId"] == 1
    
    # Verify the user address is NOT in allocations
    user_address = "anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9"
    alloc = genesis.get("alloc", [])
    
    for entry in alloc:
        assert entry.get("address") != user_address, \
            f"User address {user_address} should NOT be in mainnet genesis"


def test_genesis_files_valid_json():
    """Test that all genesis files are valid JSON."""
    for filename in ["genesis.sample.devnet.json", "genesis.sample.testnet.json", "genesis.sample.mainnet.json"]:
        genesis = _load_genesis(filename)
        assert isinstance(genesis, dict)
        assert "chainId" in genesis
        assert "alloc" in genesis
