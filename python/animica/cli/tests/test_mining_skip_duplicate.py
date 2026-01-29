"""Test that mining skips duplicate blocks and continues to mine the requested amount."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import typer
from animica.cli import mining
from typer.testing import CliRunner

runner = CliRunner()


def test_mine_blocks_skips_duplicates_and_continues(monkeypatch: Any) -> None:
    """
    Test that miner skips duplicate blocks and continues mining until reaching the requested count.
    
    Scenario:
    - Request 3 blocks
    - Block 1: accepted, not duplicate -> count=1
    - Block 2: accepted but duplicate (already found) -> skip, don't count
    - Block 3: accepted, not duplicate -> count=2
    - Block 4: accepted, not duplicate -> count=3
    
    Expected: Should mine 4 blocks total to get 3 non-duplicate blocks.
    """
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)

    class FakeRpcError(Exception):
        def __init__(self, code: int, message: str, data: dict | None = None) -> None:
            super().__init__(message)
            self.code = code
            self.message = message
            self.data = data

    block_submission_count = {"count": 0}
    mined_blocks = []

    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method: str, params: Any):
            if method == "miner.getBlockTemplate":
                block_num = block_submission_count["count"]
                return {
                    "enabled": True,
                    "header": {
                        "v": 1,
                        "chainId": 1337,
                        "height": 100 + block_num,
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
                    "target": hex((1 << 256) - 1),
                    "coinbase": {"amount": 1000},
                    "txs": [],
                    "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
                }
            if method == "miner.submitBlock":
                block_submission_count["count"] += 1
                block_num = block_submission_count["count"]
                
                # Block 2 is a duplicate (already found by another miner)
                is_duplicate = (block_num == 2)
                
                result = {
                    "accepted": True,
                    "duplicate": is_duplicate,
                    "new_head": 100 + block_num if not is_duplicate else 101,  # Height doesn't advance for duplicate
                    "credited_amount": 0 if is_duplicate else 1000,
                }
                mined_blocks.append({"block_num": block_num, "is_duplicate": is_duplicate})
                return result
            
            # Default for other methods
            return {}

    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    mock_module.RpcError = FakeRpcError

    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.errors", mock_module)

    # Disable sleep to speed up test
    monkeypatch.setattr("time.sleep", lambda x: None)

    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "3",  # Request 3 non-duplicate blocks
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )

    # Verify behavior
    assert "DUPLICATE" in result.output or "duplicate" in result.output.lower(), "Should show duplicate block message"
    assert "Successfully mined 3 block" in result.output, "Should mine 3 non-duplicate blocks"
    
    # Should have attempted 4 blocks (1 accepted, 1 duplicate, 2 more accepted = 3 total non-duplicates)
    assert block_submission_count["count"] == 4, f"Should have submitted 4 blocks (got {block_submission_count['count']})"
    
    # Should exit successfully
    assert result.exit_code == 0, f"Should complete successfully, got exit code {result.exit_code}\nOutput: {result.output}"


def test_mine_blocks_single_retry_for_stale(monkeypatch: Any) -> None:
    """
    Test that miner only retries once (not 3 times) for stale templates.
    
    Scenario:
    - Request 2 blocks
    - Block 1: stale on first attempt, accepted on second (1 retry) -> count=1
    - Block 2: accepted immediately -> count=2
    
    Expected: Should only retry once (not 3 times as before).
    """
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)

    class FakeRpcError(Exception):
        def __init__(self, code: int, message: str, data: dict | None = None) -> None:
            super().__init__(message)
            self.code = code
            self.message = message
            self.data = data

    block_attempts = {"block1": 0, "block2": 0, "current_block": 1}

    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method: str, params: Any):
            if method == "miner.getBlockTemplate":
                return {
                    "enabled": True,
                    "header": {
                        "v": 1,
                        "chainId": 1337,
                        "height": 100 + block_attempts["current_block"],
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
                    "target": hex((1 << 256) - 1),
                    "coinbase": {"amount": 1000},
                    "txs": [],
                    "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
                }
            if method == "miner.submitBlock":
                if block_attempts["current_block"] == 1:
                    block_attempts["block1"] += 1
                    # First attempt: stale, second attempt: accepted
                    if block_attempts["block1"] == 1:
                        raise FakeRpcError(-32000, "stale template", {"reason": "stale_template"})
                    else:
                        return {"accepted": True, "duplicate": False, "new_head": 101, "credited_amount": 1000}
                elif block_attempts["current_block"] == 2:
                    block_attempts["block2"] += 1
                    return {"accepted": True, "duplicate": False, "new_head": 102, "credited_amount": 1000}

    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    mock_module.RpcError = FakeRpcError

    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.errors", mock_module)

    # Track when we move to next block
    original_sleep = __import__("time").sleep
    def tracked_sleep(seconds):
        # Sleep between blocks indicates we're moving to next block
        if seconds >= 2.0:  # MIN_BLOCK_INTERVAL_SECONDS
            block_attempts["current_block"] += 1
        return original_sleep(0.001)  # Very short sleep for test speed
    
    monkeypatch.setattr("time.sleep", tracked_sleep)

    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "2",
            "--rpc-url", "http://127.0.0.1:8545",
            "--no-proxy",
        ],
    )

    # Verify only 1 retry happened (2 total attempts for block 1)
    assert block_attempts["block1"] == 2, f"Block 1 should have 2 attempts (1 retry), got {block_attempts['block1']}"
    
    # Should show retry attempt (but only 1, not 3)
    assert "stale attempt 1/1" in result.output or "Retrying" in result.output, "Should show retry message"
    
    # Both blocks should be mined
    assert "Successfully mined 2 block" in result.output, "Should mine 2 blocks"
    
    assert result.exit_code == 0, f"Should complete successfully, got exit code {result.exit_code}\nOutput: {result.output}"
