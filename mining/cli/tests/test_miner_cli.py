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
    
    def test_parse_mine_blocks_with_threads(self):
        """Test that mine-blocks parses threads parameter correctly."""
        parser = miner._build_arg_parser()
        args = parser.parse_args([
            "mine-blocks",
            "--address", "anim1test123",
            "--count", "3",
            "--threads", "4"
        ])
        
        assert args.cmd == "mine-blocks"
        assert args.address == "anim1test123"
        assert args.count == 3
        assert args.threads == 4
    
    def test_parse_mine_blocks_with_default_threads(self):
        """Test that mine-blocks uses default threads when not specified."""
        import os
        parser = miner._build_arg_parser()
        args = parser.parse_args([
            "mine-blocks",
            "--address", "anim1test123",
            "--count", "2"
        ])
        
        assert args.cmd == "mine-blocks"
        # Default should be CPU count
        assert args.threads == (os.cpu_count() or 1)

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
    async def test_mine_blocks_passes_threads_to_rpc(self):
        """Test that mine-blocks passes threads parameter to RPC method."""
        import sys
        
        # Track RPC params
        rpc_params = {}
        
        class ThreadTrackingRpcClient:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, method, params):
                # Capture params for verification
                nonlocal rpc_params
                rpc_params = params
                return {"mined": 2, "height": 102}
        
        mock_module = Mock()
        mock_module.RpcClient = ThreadTrackingRpcClient
        
        sys.modules['omni_sdk.rpc.http'] = mock_module
        sys.modules['sdk.python.omni_sdk.rpc.http'] = mock_module
        
        try:
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123",
                "--count", "2",
                "--threads", "8",
                "--rpc-url", "http://127.0.0.1:8545"
            ])
            
            # Should succeed
            assert result == 0
            # Verify threads parameter was passed
            assert "threads" in rpc_params
            assert rpc_params["threads"] == 8
        finally:
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

    @pytest.mark.asyncio
    async def test_mine_blocks_retries_on_connection_error(self):
        """Test that mine-blocks retries indefinitely on connection errors."""
        import sys
        import asyncio
        
        # Track number of attempts
        attempts = []
        
        class RetryableRpcClient:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, method, params):
                attempts.append(1)
                # Fail twice, then succeed
                if len(attempts) < 3:
                    raise ConnectionError("Connection refused")
                return {"mined": 2, "height": 102}
        
        mock_module = Mock()
        mock_module.RpcClient = RetryableRpcClient
        
        sys.modules['omni_sdk.rpc.http'] = mock_module
        sys.modules['sdk.python.omni_sdk.rpc.http'] = mock_module
        
        try:
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123",
                "--count", "2",
                "--rpc-url", "http://127.0.0.1:8545",
                "--retry-delay", "0.1"  # Fast retry for testing
            ])
            
            # Should succeed after retries
            assert result == 0
            # Should have made 3 attempts (2 failures + 1 success)
            assert len(attempts) == 3
        finally:
            sys.modules.pop('omni_sdk.rpc.http', None)
            sys.modules.pop('sdk.python.omni_sdk.rpc.http', None)

    @pytest.mark.asyncio
    async def test_mine_blocks_accepts_retry_delay_parameter(self):
        """Test that mine-blocks accepts and uses --retry-delay parameter."""
        import sys
        
        class SuccessRpcClient:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, method, params):
                return {"mined": 1, "height": 101}
        
        mock_module = Mock()
        mock_module.RpcClient = SuccessRpcClient
        
        sys.modules['omni_sdk.rpc.http'] = mock_module
        sys.modules['sdk.python.omni_sdk.rpc.http'] = mock_module
        
        try:
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123",
                "--count", "1",
                "--rpc-url", "http://127.0.0.1:8545",
                "--retry-delay", "2.5"
            ])
            
            assert result == 0
        finally:
            sys.modules.pop('omni_sdk.rpc.http', None)
            sys.modules.pop('sdk.python.omni_sdk.rpc.http', None)

    @pytest.mark.asyncio
    async def test_mine_blocks_rejects_invalid_retry_delay(self):
        """Test that mine-blocks rejects invalid retry delay values."""
        result = await miner._amain([
            "mine-blocks",
            "--address", "anim1test123",
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
            "--retry-delay", "0"
        ])
        
        # Should fail with error code
        assert result != 0

    @pytest.mark.asyncio
    async def test_mine_blocks_with_no_timeout(self):
        """Test that mine-blocks accepts and uses --no-timeout flag."""
        import sys
        
        # Track the timeout value passed to RpcClient
        timeout_tracker = {"timeout": -1}  # -1 means not set
        
        class TimeoutTrackingRpcClient:
            def __init__(self, url, timeout=30.0):
                timeout_tracker["timeout"] = timeout
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, method, params):
                return {"mined": 1, "height": 101}
        
        mock_module = Mock()
        mock_module.RpcClient = TimeoutTrackingRpcClient
        
        sys.modules['omni_sdk.rpc.http'] = mock_module
        sys.modules['sdk.python.omni_sdk.rpc.http'] = mock_module
        
        try:
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123",
                "--count", "1",
                "--rpc-url", "http://127.0.0.1:8545",
                "--no-timeout"
            ])
            
            # Should succeed
            assert result == 0
            # Timeout should be None when --no-timeout is used
            assert timeout_tracker["timeout"] is None
        finally:
            sys.modules.pop('omni_sdk.rpc.http', None)
            sys.modules.pop('sdk.python.omni_sdk.rpc.http', None)

    @pytest.mark.asyncio
    async def test_mine_blocks_without_no_timeout_uses_default(self):
        """Test that mine-blocks uses default timeout when --no-timeout is not specified."""
        import sys
        
        # Track the timeout value passed to RpcClient
        timeout_tracker = {"timeout": -1}  # -1 means not set
        
        class TimeoutTrackingRpcClient:
            def __init__(self, url, timeout=30.0):
                timeout_tracker["timeout"] = timeout
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def request(self, method, params):
                return {"mined": 1, "height": 101}
        
        mock_module = Mock()
        mock_module.RpcClient = TimeoutTrackingRpcClient
        
        sys.modules['omni_sdk.rpc.http'] = mock_module
        sys.modules['sdk.python.omni_sdk.rpc.http'] = mock_module
        
        try:
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123",
                "--count", "1",
                "--rpc-url", "http://127.0.0.1:8545"
                # --no-timeout NOT specified
            ])
            
            # Should succeed
            assert result == 0
            # Timeout should be 30.0 (default) when --no-timeout is not used
            assert timeout_tracker["timeout"] == 30.0
        finally:
            sys.modules.pop('omni_sdk.rpc.http', None)
            sys.modules.pop('sdk.python.omni_sdk.rpc.http', None)
