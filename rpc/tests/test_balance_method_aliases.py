"""
Test that balance method aliases (animica_getBalance, eth_getBalance) work correctly.

This test ensures wallet clients using different method names can access the same
balance data, fixing the disparity between explorer and wallet balance displays.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from core.genesis.loader import load_and_init_genesis
from rpc import config as rpc_config
from rpc import deps
from rpc.methods import dispatch, ensure_loaded


@pytest.fixture
def test_db():
    """Create a temporary DB with known balance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        genesis_path = Path(tmpdir) / "genesis.json"

        # Create test genesis
        genesis = {
            "chainId": 9998,
            "network": "test-aliases",
            "genesisTime": "2025-01-01T00:00:00Z",
            "unit": {"symbol": "TEST", "decimals": 9},
            "paramsRef": {"path": "spec/params.yaml"},
            "economics": {"premineTotal": "1000000000000000"},
            "alloc": [
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
        load_and_init_genesis(
            str(genesis_path),
            f"sqlite:///{db_path}",
            override_chain_id=9998,
            log=False,
        )

        yield {
            "db_uri": f"sqlite:///{db_path}",
            "chain_id": 9998,
            "test_address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
            "expected_balance": "1000000000000000",
        }


@pytest.mark.asyncio
async def test_balance_method_aliases(test_db):
    """Test that all balance method aliases return the same result."""
    # Initialize RPC context
    cfg = rpc_config.Config(
        db_uri=test_db["db_uri"],
        chain_id=test_db["chain_id"],
        host="127.0.0.1",
        port=8545,
        logging="ERROR",
    )

    # Ensure methods are loaded
    ensure_loaded()
    deps.ensure_started(cfg)

    try:
        test_addr = test_db["test_address"]
        expected = test_db["expected_balance"]

        # Test all method names
        methods_to_test = [
            "state.getBalance",       # Standard method (explorer)
            "state_getBalance",       # Snake_case alias
            "animica_getBalance",     # Wallet extension
            "eth_getBalance",         # Ethereum compatibility
        ]

        results = {}
        for method_name in methods_to_test:
            # Use dispatch to test the registered methods
            result = await dispatch(method_name, [test_addr, "latest"])
            results[method_name] = result

            # Convert hex result to int for comparison
            balance_int = int(result, 16)
            assert (
                balance_int == int(expected)
            ), f"{method_name} returned wrong balance: {result} (expected {expected})"

        # Verify all methods return the same value
        unique_results = set(results.values())
        assert (
            len(unique_results) == 1
        ), f"Methods returned different values: {results}"

        print(f"✓ All balance method aliases return consistent results: {results}")

    finally:
        # Cleanup
        try:
            ctx = deps.get_ctx()
            ctx.close()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_wallet_balance_matches_explorer(test_db):
    """
    Test that simulates wallet (animica_getBalance) and explorer (state.getBalance)
    getting the same balance value, fixing the reported disparity.
    """
    cfg = rpc_config.Config(
        db_uri=test_db["db_uri"],
        chain_id=test_db["chain_id"],
        host="127.0.0.1",
        port=8545,
        logging="ERROR",
    )

    ensure_loaded()
    deps.ensure_started(cfg)

    try:
        test_addr = test_db["test_address"]

        # Simulate explorer call
        explorer_balance = await dispatch("state.getBalance", [test_addr, "latest"])

        # Simulate wallet call
        wallet_balance = await dispatch("animica_getBalance", [test_addr, "latest"])

        # They must match
        assert (
            explorer_balance == wallet_balance
        ), f"Balance disparity detected! Explorer: {explorer_balance}, Wallet: {wallet_balance}"

        print(
            f"✓ Explorer and wallet show same balance: {explorer_balance} (hex) = {int(explorer_balance, 16)} nANM"
        )

    finally:
        try:
            ctx = deps.get_ctx()
            ctx.close()
        except Exception:
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
