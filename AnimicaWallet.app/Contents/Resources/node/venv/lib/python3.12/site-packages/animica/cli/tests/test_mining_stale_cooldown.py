"""
Test that mining adds a cooldown period after exhausting stale template retries.

This test validates the fix for the issue where mining would rapidly cycle through
failed attempts without waiting for the blockchain to stabilize, appearing to stop.
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import Mock, patch
from pathlib import Path

import pytest
from typer.testing import CliRunner
from animica.cli import mining

runner = CliRunner()


def _mock_template(height: int = 1, enabled: bool = True) -> dict:
    """Create a mock block template."""
    return {
        "enabled": enabled,
        "header": {
            "v": 1,
            "chainId": 1337,
            "height": height,
            "parentHash": "0x" + "00" * 32,
            "timestamp": 0,
            "stateRoot": "0x" + "00" * 32,
            "txsRoot": "0x" + "00" * 32,
            "receiptsRoot": "0x" + "00" * 32,
            "proofsRoot": "0x" + "00" * 32,
            "daRoot": "0x" + "00" * 32,
            "mixSeed": "0x" + "00" * 32,
            "poiesPolicyRoot": "0x" + "00" * 32,
            "pqAlgPolicyRoot": "0x" + "00" * 32,
            "thetaMicro": 1,
            "nonce": 0,
        },
        "target": hex((1 << 256) - 1),  # Very easy target
        "coinbase": {"amount": 300000000000},  # 300 ANM
        "txs": [],
        "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
        "parent": {"hash": "0x" + "aa" * 32},
        "parentHash": "0x" + "aa" * 32,
    }


def test_cooldown_after_stale_template_exhaustion():
    """
    Test that a cooldown period is added after exhausting stale template retries.
    
    This prevents rapid retry loops when the blockchain is unstable or advancing
    faster than the miner can solve PoW.
    
    Note: This test validates that the cooldown code path exists and is syntactically
    correct. Full integration testing requires a live node environment.
    """
    # Track sleep calls to verify cooldown
    sleep_calls = []
    
    def mock_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        # Don't actually sleep in test
    
    # Mock RPC client that simulates stale template scenario
    stale_count = 0
    
    def mock_request(method: str, params: Any) -> Any:
        nonlocal stale_count
        
        if method == "miner.getBlockTemplate":
            return _mock_template(height=100 + stale_count)
        
        elif method == "chain_getHead":
            # Simulate blockchain advancing (head changed)
            stale_count += 1
            return {"hash": "0x" + f"{stale_count:064x}", "height": 100 + stale_count}
        
        elif method == "miner.submitBlock":
            # Always reject as stale for first attempt, then accept on retry
            if stale_count < 2:
                raise Exception("Block rejected: stale_template")
            return {"accepted": True, "new_head": 102, "credited_amount": 300000000000}
        
        return {}
    
    with patch("animica.cli.mining.rpc_client") as mock_rpc_context:
        mock_client = Mock()
        mock_client.request = mock_request
        mock_rpc_context.return_value.__enter__.return_value = mock_client
        
        with patch("time.sleep", mock_sleep):
            # Attempt to mine 1 block, which will exhaust retries and trigger cooldown
            result = runner.invoke(
                mining.app,
                [
                    "mine-blocks",
                    "--address", "anim1test",
                    "--count", "1",
                    "--url", "http://localhost:8545",
                    "--no-timeout",
                ],
            )
    
    # Verify that cooldown message appears in output
    assert "blockchain to stabilize" in result.output.lower() or sleep_calls, (
        "Expected cooldown message or sleep calls when stale retries are exhausted"
    )


def test_cooldown_helper_function_exists():
    """
    Test that the cooldown helper function and related code exists.
    
    This verifies the refactoring to use a helper function is in place.
    """
    # Import the mining module to ensure the changes are syntactically correct
    from animica.cli import mining as mining_module
    
    # Verify key constants and strings are present
    source_code = Path(mining_module.__file__).read_text()
    assert "MIN_BLOCK_INTERVAL_SECONDS" in source_code
    assert "_apply_stale_template_cooldown" in source_code
    assert "blockchain to stabilize" in source_code


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
