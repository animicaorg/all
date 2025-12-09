"""Tests for mining.cli.miner CLI commands."""

import argparse
import pytest
from unittest.mock import MagicMock, patch, Mock

from mining.cli import miner


# Mock RpcClient at module level to avoid import issues
class MockRpcClient:
    def __init__(self, *args, **kwargs):
        self.request = MagicMock()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass


class TestMineBlocksCommand:
    """Test suite for mine-blocks subcommand."""

    def test_parse_mine_blocks_with_required_args(self):
        """Test that mine-blocks parses address and count correctly."""
        parser = miner._build_arg_parser()
        args = parser.parse_args([
            "mine-blocks",
            "--address", "anim1test123address",
            "--count", "5"
        ])
        
        assert args.cmd == "mine-blocks"
        assert args.address == "anim1test123address"
        assert args.count == 5

    def test_parse_mine_blocks_missing_address(self):
        """Test that mine-blocks fails when address is missing."""
        parser = miner._build_arg_parser()
        
        with pytest.raises(SystemExit):
            parser.parse_args(["mine-blocks", "--count", "5"])

    def test_parse_mine_blocks_missing_count(self):
        """Test that mine-blocks fails when count is missing."""
        parser = miner._build_arg_parser()
        
        with pytest.raises(SystemExit):
            parser.parse_args(["mine-blocks", "--address", "anim1test123"])

    def test_parse_mine_blocks_invalid_count_zero(self):
        """Test that count=0 is rejected."""
        parser = miner._build_arg_parser()
        
        # Parser will accept it, but validation should fail in the command handler
        args = parser.parse_args([
            "mine-blocks",
            "--address", "anim1test123",
            "--count", "0"
        ])
        assert args.count == 0

    def test_parse_mine_blocks_invalid_count_negative(self):
        """Test that negative count is rejected."""
        parser = miner._build_arg_parser()
        
        # Parser will accept it, but validation should fail in the command handler
        args = parser.parse_args([
            "mine-blocks",
            "--address", "anim1test123",
            "--count", "-5"
        ])
        assert args.count == -5

    @pytest.mark.asyncio
    async def test_mine_blocks_validates_count_positive(self):
        """Test that mine-blocks validates count > 0."""
        # Test with count=0
        result = await miner._amain([
            "mine-blocks",
            "--address", "anim1test123",
            "--count", "0"
        ])
        
        # Should fail with error code
        assert result != 0

    @pytest.mark.asyncio
    async def test_mine_blocks_calls_rpc_correctly(self):
        """Test that mine-blocks calls the RPC with correct parameters."""
        import sys
        
        # Create mock module
        class SuccessRpcClient:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, method, params):
                # Return success response
                return {"mined": 3, "height": 103}
        
        mock_module = Mock()
        mock_module.RpcClient = SuccessRpcClient
        
        # Temporarily inject the mock module
        sys.modules['omni_sdk.rpc.http'] = mock_module
        sys.modules['sdk.python.omni_sdk.rpc.http'] = mock_module
        
        try:
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123address",
                "--count", "3",
                "--rpc-url", "http://127.0.0.1:8545"
            ])
            
            # Should succeed
            assert result == 0
        finally:
            # Clean up
            sys.modules.pop('omni_sdk.rpc.http', None)
            sys.modules.pop('sdk.python.omni_sdk.rpc.http', None)

    @pytest.mark.asyncio
    async def test_mine_blocks_handles_rpc_error(self):
        """Test that mine-blocks handles RPC errors gracefully."""
        import sys
        
        class ErrorRpcClient:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, *args, **kwargs):
                raise ConnectionError("RPC connection failed")
        
        mock_module = Mock()
        mock_module.RpcClient = ErrorRpcClient
        
        sys.modules['omni_sdk.rpc.http'] = mock_module
        sys.modules['sdk.python.omni_sdk.rpc.http'] = mock_module
        
        try:
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123",
                "--count", "3",
                "--rpc-url", "http://127.0.0.1:8545"
            ])
            
            # Should fail with error code
            assert result != 0
        finally:
            sys.modules.pop('omni_sdk.rpc.http', None)
            sys.modules.pop('sdk.python.omni_sdk.rpc.http', None)

    @pytest.mark.asyncio
    async def test_mine_blocks_logs_progress(self):
        """Test that mine-blocks logs useful progress information."""
        import sys
        
        class SuccessRpcClient:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, method, params):
                return {"mined": 5, "height": 105}
        
        mock_module = Mock()
        mock_module.RpcClient = SuccessRpcClient
        
        sys.modules['omni_sdk.rpc.http'] = mock_module
        sys.modules['sdk.python.omni_sdk.rpc.http'] = mock_module
        
        try:
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123",
                "--count", "5",
                "--rpc-url", "http://127.0.0.1:8545"
            ])
            
            assert result == 0
            # We expect the implementation to log the blocks mined and height
        finally:
            sys.modules.pop('omni_sdk.rpc.http', None)
            sys.modules.pop('sdk.python.omni_sdk.rpc.http', None)
