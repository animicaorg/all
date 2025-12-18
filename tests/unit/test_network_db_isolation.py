"""
Test per-network database isolation
====================================

Verifies that different networks (mainnet, testnet, devnet) use separate
database directories to prevent state contamination when switching networks.

This guards against:
1. Networks sharing the same DB file
2. State from one network bleeding into another
3. Loss of data when switching between networks
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from pathlib import Path

import pytest


def test_core_config_per_network_data_dir():
    """Test that core.config uses distinct data directories per chain ID."""
    from core.config import ChainConfig, PathsConfig
    
    # Test mainnet (chain 1)
    mainnet_chain = ChainConfig(chain_id=1, network_name="mainnet")
    mainnet_paths = PathsConfig.defaults(mainnet_chain)
    assert "chain-1" in str(mainnet_paths.data_dir), \
        f"Mainnet should use chain-1 directory, got {mainnet_paths.data_dir}"
    
    # Test testnet (chain 2)
    testnet_chain = ChainConfig(chain_id=2, network_name="testnet")
    testnet_paths = PathsConfig.defaults(testnet_chain)
    assert "chain-2" in str(testnet_paths.data_dir), \
        f"Testnet should use chain-2 directory, got {testnet_paths.data_dir}"
    
    # Test devnet (chain 1337)
    devnet_chain = ChainConfig(chain_id=1337, network_name="devnet")
    devnet_paths = PathsConfig.defaults(devnet_chain)
    assert "chain-1337" in str(devnet_paths.data_dir), \
        f"Devnet should use chain-1337 directory, got {devnet_paths.data_dir}"
    
    # Ensure directories are distinct
    assert mainnet_paths.data_dir != testnet_paths.data_dir
    assert mainnet_paths.data_dir != devnet_paths.data_dir
    assert testnet_paths.data_dir != devnet_paths.data_dir


def test_rpc_config_per_network_db_uri():
    """Test that RPC config uses distinct DB URIs per network."""
    # Save original env
    orig_network = os.environ.get("ANIMICA_NETWORK")
    orig_db_uri = os.environ.get("ANIMICA_RPC_DB_URI")
    orig_chain_id = os.environ.get("ANIMICA_CHAIN_ID")
    
    try:
        # Clear env for clean test
        for key in ["ANIMICA_NETWORK", "ANIMICA_RPC_DB_URI", "ANIMICA_CHAIN_ID"]:
            os.environ.pop(key, None)
        
        # Test mainnet
        os.environ["ANIMICA_NETWORK"] = "mainnet"
        import rpc.config
        importlib.reload(rpc.config)
        from rpc.config import load
        mainnet_cfg = load()
        assert mainnet_cfg.chain_id == 1, f"Mainnet should be chain 1, got {mainnet_cfg.chain_id}"
        assert "chain-1" in mainnet_cfg.db_uri, \
            f"Mainnet DB should use chain-1 path, got {mainnet_cfg.db_uri}"
        
        # Test testnet
        os.environ["ANIMICA_NETWORK"] = "testnet"
        # Reload config module to pick up env change
        importlib.reload(rpc.config)
        testnet_cfg = load()
        assert testnet_cfg.chain_id == 2, f"Testnet should be chain 2, got {testnet_cfg.chain_id}"
        assert "chain-2" in testnet_cfg.db_uri, \
            f"Testnet DB should use chain-2 path, got {testnet_cfg.db_uri}"
        
        # Test devnet
        os.environ["ANIMICA_NETWORK"] = "devnet"
        importlib.reload(rpc.config)
        devnet_cfg = load()
        assert devnet_cfg.chain_id == 1337, f"Devnet should be chain 1337, got {devnet_cfg.chain_id}"
        assert "chain-1337" in devnet_cfg.db_uri, \
            f"Devnet DB should use chain-1337 path, got {devnet_cfg.db_uri}"
        
        # Ensure DB URIs are distinct
        assert mainnet_cfg.db_uri != testnet_cfg.db_uri
        assert mainnet_cfg.db_uri != devnet_cfg.db_uri
        assert testnet_cfg.db_uri != devnet_cfg.db_uri
        
    finally:
        # Restore original env
        if orig_network is not None:
            os.environ["ANIMICA_NETWORK"] = orig_network
        else:
            os.environ.pop("ANIMICA_NETWORK", None)
        
        if orig_db_uri is not None:
            os.environ["ANIMICA_RPC_DB_URI"] = orig_db_uri
        else:
            os.environ.pop("ANIMICA_RPC_DB_URI", None)
        
        if orig_chain_id is not None:
            os.environ["ANIMICA_CHAIN_ID"] = orig_chain_id
        else:
            os.environ.pop("ANIMICA_CHAIN_ID", None)
        
        # Reload config to restore original state
        importlib.reload(rpc.config)


def test_rpc_config_migrates_legacy_profile_db(tmp_path, monkeypatch):
    """Legacy profile-based DBs should be migrated into chain-specific paths."""

    data_root = tmp_path / "data"
    legacy_dir = data_root / "mainnet"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "chain.db"
    legacy_db.write_bytes(b"legacy-mainnet-db")

    monkeypatch.setenv("ANIMICA_DATA_DIR", str(data_root))
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    monkeypatch.delenv("ANIMICA_RPC_DB_URI", raising=False)

    import rpc.config as rpc_config
    importlib.reload(rpc_config)
    cfg = rpc_config.load()

    expected_db = data_root / "chain-1" / "animica.db"
    assert expected_db.exists(), "Expected mainnet DB to be materialized"
    assert expected_db.read_bytes() == b"legacy-mainnet-db"
    assert str(expected_db) in cfg.db_uri


def test_network_switch_does_not_contaminate_state():
    """
    Simulate switching networks and verify that each network maintains
    separate state.
    """
    from core.genesis.loader import load_and_init_genesis
    from core.db.state_db import StateDB
    from core.utils.address import address_to_bytes
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create separate genesis files for two networks
        mainnet_genesis = {
            "chainId": 1,
            "network": "mainnet-test",
            "genesisTime": "2025-01-01T00:00:00Z",
            "unit": {"symbol": "ANM", "decimals": 9},
            "paramsRef": {"path": "spec/params.yaml"},
            "economics": {"premineTotal": "1000000"},
            "alloc": [
                {"address": "system:mainnet", "nonce": 0, "balance": "1000000"}
            ],
            "consensus": {"initialThetaMicro": 1000000},
        }
        
        devnet_genesis = {
            "chainId": 1337,
            "network": "devnet-test",
            "genesisTime": "2025-01-01T00:00:00Z",
            "unit": {"symbol": "ANM", "decimals": 9},
            "paramsRef": {"path": "spec/params.yaml"},
            "economics": {"premineTotal": "2000000"},
            "alloc": [
                {"address": "system:devnet", "nonce": 0, "balance": "2000000"}
            ],
            "consensus": {"initialThetaMicro": 1000000},
        }
        
        import json
        mainnet_genesis_path = tmpdir / "mainnet_genesis.json"
        devnet_genesis_path = tmpdir / "devnet_genesis.json"
        
        mainnet_genesis_path.write_text(json.dumps(mainnet_genesis))
        devnet_genesis_path.write_text(json.dumps(devnet_genesis))
        
        # Initialize mainnet with chain-1 DB
        mainnet_db = tmpdir / "chain-1" / "animica.db"
        mainnet_db.parent.mkdir(parents=True, exist_ok=True)
        
        mainnet_result = load_and_init_genesis(
            str(mainnet_genesis_path),
            f"sqlite:///{mainnet_db}",
            override_chain_id=1,
            log=False,
        )
        
        # Verify mainnet state
        mainnet_kv = mainnet_result["kv"]
        mainnet_state = StateDB(mainnet_kv)
        mainnet_addr = address_to_bytes("system:mainnet")
        mainnet_balance = mainnet_state.get_balance(mainnet_addr)
        assert mainnet_balance == 1000000, \
            f"Mainnet balance should be 1000000, got {mainnet_balance}"
        mainnet_kv.close()
        
        # Initialize devnet with chain-1337 DB (separate directory)
        devnet_db = tmpdir / "chain-1337" / "animica.db"
        devnet_db.parent.mkdir(parents=True, exist_ok=True)
        
        devnet_result = load_and_init_genesis(
            str(devnet_genesis_path),
            f"sqlite:///{devnet_db}",
            override_chain_id=1337,
            log=False,
        )
        
        # Verify devnet state
        devnet_kv = devnet_result["kv"]
        devnet_state = StateDB(devnet_kv)
        devnet_addr = address_to_bytes("system:devnet")
        devnet_balance = devnet_state.get_balance(devnet_addr)
        assert devnet_balance == 2000000, \
            f"Devnet balance should be 2000000, got {devnet_balance}"
        
        # Verify mainnet address does not exist in devnet
        mainnet_in_devnet = devnet_state.get_balance(mainnet_addr)
        assert mainnet_in_devnet == 0, \
            "Mainnet address should not exist in devnet state"
        
        devnet_kv.close()
        
        # Re-open mainnet DB and verify state is unchanged
        from core.db.sqlite import SQLiteKV
        mainnet_kv_reopen = SQLiteKV(str(mainnet_db))
        mainnet_state_reopen = StateDB(mainnet_kv_reopen)
        
        mainnet_balance_after = mainnet_state_reopen.get_balance(mainnet_addr)
        assert mainnet_balance_after == 1000000, \
            f"Mainnet state should be unchanged after devnet init, got {mainnet_balance_after}"
        
        # Verify devnet address does not exist in mainnet
        devnet_in_mainnet = mainnet_state_reopen.get_balance(devnet_addr)
        assert devnet_in_mainnet == 0, \
            "Devnet address should not exist in mainnet state"
        
        mainnet_kv_reopen.close()


def test_default_network_is_mainnet():
    """Test that the system defaults to mainnet when no network is specified."""
    from core.config import ChainConfig
    
    # Save and clear env
    orig_network = os.environ.get("ANIMICA_NETWORK")
    orig_chain_id = os.environ.get("ANIMICA_CHAIN_ID")
    
    try:
        os.environ.pop("ANIMICA_NETWORK", None)
        os.environ.pop("ANIMICA_CHAIN_ID", None)
        
        # Infer from empty env should default to mainnet
        chain = ChainConfig.infer_from_env()
        assert chain.chain_id == 1, f"Default should be mainnet (chain 1), got {chain.chain_id}"
        assert chain.network_name == "mainnet", f"Default network name should be mainnet, got {chain.network_name}"
        
    finally:
        # Restore env
        if orig_network is not None:
            os.environ["ANIMICA_NETWORK"] = orig_network
        if orig_chain_id is not None:
            os.environ["ANIMICA_CHAIN_ID"] = orig_chain_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
