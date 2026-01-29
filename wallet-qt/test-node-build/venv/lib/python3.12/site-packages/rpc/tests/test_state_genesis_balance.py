"""
Test that RPC state.getBalance returns correct values for genesis-seeded accounts.

This test guards against the regression where:
- Genesis loader stores addresses with one encoding (UTF-8 string)
- RPC state_service parses addresses with a different encoding (bech32 payload)
- Result: balance lookups fail, returning 0 instead of genesis balance

The fix ensures both genesis loader and RPC use canonical address_to_bytes().
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from core.db import open_kv
from core.db.state_db import StateDB
from core.genesis.loader import load_and_init_genesis
from core.utils.address import address_to_bytes
from rpc import config as rpc_config
from rpc import deps
from rpc.state_service import get_balance


@pytest.fixture
def test_genesis_db():
    """Create a temporary genesis DB with known balances."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_genesis.db"
        genesis_path = Path(tmpdir) / "test_genesis.json"

        # Create test genesis with bech32 and system addresses
        genesis = {
            "chainId": 9999,
            "network": "test-rpc-state",
            "genesisTime": "2025-01-01T00:00:00Z",
            "unit": {"symbol": "TEST", "decimals": 9},
            "paramsRef": {"path": "spec/params.yaml"},
            "economics": {"premineTotal": "2000000000000000"},
            "alloc": [
                {
                    "address": "system:treasury",
                    "nonce": 0,
                    "balance": "1000000000000000",
                },
                {
                    "address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
                    "nonce": 0,
                    "balance": "1000000000000000",
                },
            ],
            "consensus": {"initialThetaMicro": 1000000},
        }

        genesis_path.write_text(json.dumps(genesis))

        # Initialize genesis
        result = load_and_init_genesis(
            str(genesis_path),
            f"sqlite:///{db_path}",
            override_chain_id=9999,
            log=False,
        )

        yield {
            "db_uri": f"sqlite:///{db_path}",
            "db_path": db_path,
            "chain_id": 9999,
            "balances": {
                "system:treasury": 1000000000000000,
                "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz": 1000000000000000,
            },
        }


def test_state_db_direct_access(test_genesis_db):
    """Test that StateDB returns correct balances when queried directly."""
    kv = open_kv(test_genesis_db["db_uri"])
    state = StateDB(kv)

    # Test system address
    sys_addr_bytes = address_to_bytes("system:treasury")
    sys_balance = state.get_balance(sys_addr_bytes)
    assert (
        sys_balance == test_genesis_db["balances"]["system:treasury"]
    ), f"System address balance mismatch: {sys_balance}"

    # Test bech32 address
    bech_addr = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    bech_addr_bytes = address_to_bytes(bech_addr)
    bech_balance = state.get_balance(bech_addr_bytes)
    assert (
        bech_balance == test_genesis_db["balances"][bech_addr]
    ), f"Bech32 address balance mismatch: {bech_balance}"

    kv.close()


def test_rpc_state_service_get_balance(test_genesis_db):
    """Test that rpc.state_service.get_balance returns correct balances."""
    # Initialize RPC context
    cfg = rpc_config.Config(
        db_uri=test_genesis_db["db_uri"],
        chain_id=test_genesis_db["chain_id"],
        host="127.0.0.1",
        port=8545,
        logging="ERROR",
    )

    deps.ensure_started(cfg)

    try:
        # Test system address
        sys_balance = get_balance("system:treasury")
        assert (
            sys_balance == test_genesis_db["balances"]["system:treasury"]
        ), f"RPC service returned wrong balance for system address: {sys_balance}"

        # Test bech32 address
        bech_addr = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
        bech_balance = get_balance(bech_addr)
        assert (
            bech_balance == test_genesis_db["balances"][bech_addr]
        ), f"RPC service returned wrong balance for bech32 address: {bech_balance}"

    finally:
        # Cleanup
        try:
            ctx = deps.get_ctx()
            ctx.close()
        except Exception:
            pass


def test_rpc_method_state_get_balance(test_genesis_db):
    """Test that the state.getBalance RPC method returns correct values."""
    from rpc.methods.state import state_get_balance

    # Initialize RPC context
    cfg = rpc_config.Config(
        db_uri=test_genesis_db["db_uri"],
        chain_id=test_genesis_db["chain_id"],
        host="127.0.0.1",
        port=8545,
        logging="ERROR",
    )

    deps.ensure_started(cfg)

    try:
        # Test system address
        sys_result = state_get_balance("system:treasury")
        sys_balance_int = int(sys_result, 16)
        assert (
            sys_balance_int == test_genesis_db["balances"]["system:treasury"]
        ), f"RPC method returned wrong balance for system address: {sys_result} ({sys_balance_int})"

        # Test bech32 address
        bech_addr = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
        bech_result = state_get_balance(bech_addr)
        bech_balance_int = int(bech_result, 16)
        assert (
            bech_balance_int == test_genesis_db["balances"][bech_addr]
        ), f"RPC method returned wrong balance for bech32 address: {bech_result} ({bech_balance_int})"

        # Test non-existent address returns 0
        zero_result = state_get_balance(
            "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq8xyuud"
        )
        assert (
            zero_result == "0x0"
        ), f"Expected 0x0 for non-existent address, got {zero_result}"

    finally:
        # Cleanup
        try:
            ctx = deps.get_ctx()
            ctx.close()
        except Exception:
            pass


def test_address_encoding_consistency():
    """Test that address_to_bytes produces consistent results."""
    # System address should be UTF-8 encoded
    sys_addr = "system:treasury"
    sys_bytes = address_to_bytes(sys_addr)
    assert sys_bytes == b"system:treasury", f"System address encoding mismatch"
    assert len(sys_bytes) == 15, f"System address length mismatch"

    # Bech32 address should be decoded to payload (34 bytes: alg_id + digest)
    bech_addr = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    bech_bytes = address_to_bytes(bech_addr)
    assert len(bech_bytes) == 34, f"Bech32 payload should be 34 bytes, got {len(bech_bytes)}"

    # Encoding should be deterministic
    assert address_to_bytes(sys_addr) == sys_bytes
    assert address_to_bytes(bech_addr) == bech_bytes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
