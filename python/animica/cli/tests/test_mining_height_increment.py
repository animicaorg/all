"""Test that mining multiple blocks shows incrementing heights in output."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from animica.cli import mining

runner = CliRunner()


def test_multiple_blocks_show_incrementing_heights() -> None:
    """Test that mining multiple blocks displays incrementing heights in output."""
    
    # Track which block we're on
    call_count = {"count": 0}
    
    def mock_request(method: str, params: Any = None) -> dict[str, Any]:
        """Mock RPC client request method."""
        if method == "miner.getBlockTemplate":
            # Always return the same template (this simulates the bug scenario)
            return {
                "enabled": True,
                "header": {
                    "height": 1000,  # Template always shows height 1000
                    "parentHash": "0x" + "00" * 32,
                    "timestamp": 1000000,
                    "stateRoot": "0x" + "00" * 32,
                    "txsRoot": "0x" + "00" * 32,
                    "receiptsRoot": "0x" + "00" * 32,
                    "proofsRoot": "0x" + "00" * 32,
                    "daRoot": "0x" + "00" * 32,
                    "mixSeed": "0x" + "00" * 32,
                    "poiesPolicyRoot": "0x" + "00" * 32,
                    "pqAlgPolicyRoot": "0x" + "00" * 32,
                    "thetaMicro": 1000000,
                    "workType": 0,
                    "nonce": 0,
                    "extra": "0x",
                },
                "target": "0x" + "ff" * 32,
                "coinbase": {"amount": 5000000000},
                "txs": [],
                "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
            }
        if method == "miner.submitBlock":
            # Return incrementing height in new_head
            call_count["count"] += 1
            return {
                "accepted": True,
                "duplicate": False,
                "credited_amount": 5000000000,
                "new_head": 1000 + call_count["count"],  # Height increments: 1001, 1002, 1003
                "block_hash": f"0x{'ab' * 32}",
            }
        return {}
    
    # Mock the RpcClient
    with patch("animica.cli.mining.RpcClient") as mock_rpc_class:
        mock_client = Mock()
        mock_client.request = Mock(side_effect=mock_request)
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_rpc_class.return_value = mock_client
        
        # Mine 3 blocks
        result = runner.invoke(
            mining.app,
            [
                "mine",
                "--count", "3",
                "--address", "0x1234567890123456789012345678901234567890",
                "--rpc-url", "http://localhost:8545",
                "--device", "cpu",
            ],
        )
        
        # Should succeed
        assert result.exit_code == 0, f"Exit code: {result.exit_code}\nOutput: {result.output}"
        
        # Verify output shows incrementing heights
        assert "height: 1001" in result.output, f"Missing 1001 in output:\n{result.output}"
        assert "height: 1002" in result.output, f"Missing 1002 in output:\n{result.output}"
        assert "height: 1003" in result.output, f"Missing 1003 in output:\n{result.output}"
        assert "FOUND: Block 2/3 PoW (height: 1002" in result.output, (
            f"Missing FOUND height 1002 in output:\n{result.output}"
        )
        assert "FOUND: Block 3/3 PoW (height: 1003" in result.output, (
            f"Missing FOUND height 1003 in output:\n{result.output}"
        )
        
        # Verify we don't show the template height (1000) in ACCEPTED messages
        # Count how many times "ACCEPTED" appears with height 1000
        accepted_with_1000 = result.output.count("ACCEPTED") and result.output.count("height: 1000")
        assert accepted_with_1000 == 0, f"Should not show height 1000 in ACCEPTED messages:\n{result.output}"
        
        # Verify final summary shows correct height
        assert "New chain height: 1003" in result.output, f"Missing final height in output:\n{result.output}"
