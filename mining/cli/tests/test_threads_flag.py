"""Tests for --threads flag in mine-blocks command."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def test_threads_flag_parsing():
    """Test that --threads flag is parsed correctly."""
    from mining.cli.miner import _build_arg_parser
    
    parser = _build_arg_parser()
    
    # Test with explicit threads value
    args = parser.parse_args([
        "mine-blocks",
        "--address", "anim1test123",
        "--count", "5",
        "--threads", "8"
    ])
    
    assert args.cmd == "mine-blocks"
    assert args.address == "anim1test123"
    assert args.count == 5
    assert args.threads == 8


def test_threads_flag_default():
    """Test that --threads defaults to CPU count."""
    from mining.cli.miner import _build_arg_parser
    
    parser = _build_arg_parser()
    
    # Test without threads flag
    args = parser.parse_args([
        "mine-blocks",
        "--address", "anim1test123",
        "--count", "5"
    ])
    
    # Should default to CPU count
    expected_default = os.cpu_count() or 1
    assert args.threads == expected_default


def test_threads_flag_validation():
    """Test that --threads validates thread count."""
    from mining.cli.miner import _build_arg_parser
    
    parser = _build_arg_parser()
    
    # Test with positive thread count
    args = parser.parse_args([
        "mine-blocks",
        "--address", "anim1test123",
        "--count", "5",
        "--threads", "16"
    ])
    assert args.threads == 16
    
    # Test with thread count of 1
    args = parser.parse_args([
        "mine-blocks",
        "--address", "anim1test123",
        "--count", "5",
        "--threads", "1"
    ])
    assert args.threads == 1


def test_threads_in_start_command():
    """Test that --threads exists in start command (baseline verification)."""
    from mining.cli.miner import _build_arg_parser
    
    parser = _build_arg_parser()
    
    # Test start command with threads
    args = parser.parse_args([
        "start",
        "--threads", "4"
    ])
    
    assert args.cmd == "start"
    assert args.threads == 4


def test_threads_parameter_passing():
    """Test that threads parameter is passed to RPC correctly."""
    from mining.cli.miner import _run_mine_blocks
    import asyncio
    import logging
    from unittest.mock import MagicMock, patch
    from argparse import Namespace
    
    # Mock RpcClient
    mock_client = MagicMock()
    mock_client_instance = MagicMock()
    mock_client_instance.request = MagicMock(return_value={
        "mined": 2,
        "height": 10,
        "totalReward": 1000000000,
        "rewards": [
            {"height": 9, "reward": 500000000},
            {"height": 10, "reward": 500000000}
        ]
    })
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)
    
    # Create args with threads
    args = Namespace(
        address="anim1test123",
        count=2,
        threads=8,
        rpc_url="http://127.0.0.1:8547",
        log_level="info"
    )
    
    log = logging.getLogger("test")
    
    # Patch RpcClient import
    with patch("mining.cli.miner.RPCClient", mock_client):
        # Run the function
        result = asyncio.run(_run_mine_blocks(args, log))
    
    # Should have succeeded
    assert result == 0
    
    # Check that RPC was called with threads parameter
    calls = mock_client_instance.request.call_args_list
    assert len(calls) >= 1
    
    # First call should include threads
    method, params = calls[0][0]
    assert method == "miner.mine"
    assert "threads" in params
    assert params["threads"] == 8


if __name__ == "__main__":
    # Run tests directly
    test_threads_flag_parsing()
    print("✓ Threads flag parsing test passed")
    
    test_threads_flag_default()
    print("✓ Threads flag default test passed")
    
    test_threads_flag_validation()
    print("✓ Threads flag validation test passed")
    
    test_threads_in_start_command()
    print("✓ Threads in start command test passed")
    
    test_threads_parameter_passing()
    print("✓ Threads parameter passing test passed")
    
    print("\n✓ All tests passed!")
