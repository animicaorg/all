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


def test_threads_parameter_in_args():
    """Test that threads parameter is available in parsed arguments for RPC call."""
    from mining.cli.miner import _build_arg_parser
    from argparse import Namespace
    
    parser = _build_arg_parser()
    
    # Parse mine-blocks command with threads
    args = parser.parse_args([
        "mine-blocks",
        "--address", "anim1test123",
        "--count", "5",
        "--threads", "8",
        "--rpc-url", "http://127.0.0.1:8547"
    ])
    
    # Verify all parameters are present
    assert hasattr(args, "cmd")
    assert hasattr(args, "address")
    assert hasattr(args, "count")
    assert hasattr(args, "threads")
    assert hasattr(args, "rpc_url")
    
    # Verify values
    assert args.cmd == "mine-blocks"
    assert args.address == "anim1test123"
    assert args.count == 5
    assert args.threads == 8
    assert args.rpc_url == "http://127.0.0.1:8547"
    
    # Test that threads can be used to construct RPC params
    rpc_params = {
        "count": args.count,
        "address": args.address,
        "threads": args.threads
    }
    
    assert rpc_params["threads"] == 8
    assert rpc_params["count"] == 5
    assert rpc_params["address"] == "anim1test123"


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
    
    test_threads_parameter_in_args()
    print("✓ Threads parameter in args test passed")
    
    print("\n✓ All tests passed!")
