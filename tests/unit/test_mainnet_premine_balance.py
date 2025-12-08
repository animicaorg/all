"""
Test mainnet premine balance initialization
============================================

Regression test to ensure that the mainnet genesis (core/genesis/genesis.json)
correctly allocates the premine balance to the expected address, and that this
balance is accessible via RPC state.getBalance after genesis initialization.

This guards against:
1. Missing premine allocation in genesis.json
2. Incorrect address encoding preventing balance lookups
3. Genesis not being loaded when defaulting to mainnet
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from core.db import open_kv
from core.db.state_db import StateDB
from core.genesis.loader import load_and_init_genesis
from core.utils.address import address_to_bytes


MAINNET_PREMINE_ADDRESS = "anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f"
MAINNET_PREMINE_BALANCE = 81000000000000000  # 81,000,000 ANM in base units


@pytest.fixture
def mainnet_genesis_path():
    """Get path to the canonical mainnet genesis file."""
    repo_root = Path(__file__).resolve().parents[2]
    genesis_path = repo_root / "core" / "genesis" / "genesis.json"
    assert genesis_path.exists(), f"Mainnet genesis not found at {genesis_path}"
    return genesis_path


@pytest.fixture
def mainnet_genesis_data(mainnet_genesis_path):
    """Load mainnet genesis data for validation."""
    with open(mainnet_genesis_path) as f:
        return json.load(f)


def test_mainnet_genesis_has_premine_allocation(mainnet_genesis_data):
    """Verify that mainnet genesis.json contains the premine allocation."""
    assert mainnet_genesis_data["chainId"] == 1, "Expected chainId 1 for mainnet"
    
    alloc = mainnet_genesis_data.get("alloc", [])
    assert len(alloc) > 0, "Genesis alloc is empty"
    
    # Find the premine address in alloc
    premine_entry = None
    for entry in alloc:
        if entry.get("address") == MAINNET_PREMINE_ADDRESS:
            premine_entry = entry
            break
    
    assert premine_entry is not None, f"Premine address {MAINNET_PREMINE_ADDRESS} not found in genesis alloc"
    
    # Verify the balance matches expected premine total
    balance = int(premine_entry.get("balance", 0))
    assert balance == MAINNET_PREMINE_BALANCE, \
        f"Premine balance mismatch: expected {MAINNET_PREMINE_BALANCE}, got {balance}"


def test_mainnet_premine_loads_to_state_db(mainnet_genesis_path):
    """Verify that mainnet genesis correctly initializes the premine balance in StateDB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "mainnet_test.db"
        
        # Initialize genesis
        result = load_and_init_genesis(
            str(mainnet_genesis_path),
            f"sqlite:///{db_path}",
            override_chain_id=1,
            log=False,
        )
        
        # Verify genesis was initialized
        assert result["head_height"] == 0, "Genesis should be at height 0"
        assert result["genesis"]["chainId"] == 1, "Should be mainnet chainId"
        
        # Check the premine balance in StateDB
        kv = result["kv"]
        state = StateDB(kv)
        
        premine_addr_bytes = address_to_bytes(MAINNET_PREMINE_ADDRESS)
        premine_balance = state.get_balance(premine_addr_bytes)
        
        assert premine_balance == MAINNET_PREMINE_BALANCE, \
            f"Premine balance in StateDB should be {MAINNET_PREMINE_BALANCE}, got {premine_balance}"
        
        kv.close()


def test_mainnet_premine_accessible_via_rpc():
    """Verify that the premine balance is accessible via RPC state.getBalance."""
    from rpc import config as rpc_config
    from rpc import deps
    from rpc.state_service import get_balance
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "mainnet_rpc_test.db"
        genesis_path = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"
        
        # Initialize RPC with mainnet configuration
        cfg = rpc_config.Config(
            db_uri=f"sqlite:///{db_path}",
            chain_id=1,
            host="127.0.0.1",
            port=8545,
            logging="ERROR",
            genesis_path=genesis_path,
        )
        
        deps.ensure_started(cfg)
        
        try:
            # Query the premine balance via RPC
            balance = get_balance(MAINNET_PREMINE_ADDRESS)
            
            assert balance == MAINNET_PREMINE_BALANCE, \
                f"RPC getBalance should return {MAINNET_PREMINE_BALANCE} for premine address, got {balance}"
            
            # Also test that a non-existent address returns 0
            zero_balance = get_balance("anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq8xyuud")
            assert zero_balance == 0, f"Non-existent address should have balance 0, got {zero_balance}"
            
        finally:
            # Cleanup
            try:
                ctx = deps.get_ctx()
                ctx.close()
            except Exception:
                pass


def test_mainnet_premine_via_rpc_method():
    """Verify state.getBalance RPC method returns correct premine balance."""
    from rpc import config as rpc_config
    from rpc import deps
    from rpc.methods.state import state_get_balance
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "mainnet_rpc_method_test.db"
        genesis_path = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"
        
        # Initialize RPC with mainnet configuration
        cfg = rpc_config.Config(
            db_uri=f"sqlite:///{db_path}",
            chain_id=1,
            host="127.0.0.1",
            port=8545,
            logging="ERROR",
            genesis_path=genesis_path,
        )
        
        deps.ensure_started(cfg)
        
        try:
            # Query via RPC method (returns hex string)
            hex_balance = state_get_balance(MAINNET_PREMINE_ADDRESS)
            balance = int(hex_balance, 16)
            
            assert balance == MAINNET_PREMINE_BALANCE, \
                f"RPC method state.getBalance should return {MAINNET_PREMINE_BALANCE}, got {balance} (hex: {hex_balance})"
            
        finally:
            # Cleanup
            try:
                ctx = deps.get_ctx()
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
