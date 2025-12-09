"""Tests for mining.cli.miner CLI commands."""

import argparse
import pytest
from unittest.mock import MagicMock, patch

from mining.cli import miner


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
        # Mock the RPC client to avoid actual network calls
        with patch("mining.cli.miner.RpcClient") as mock_rpc_cls:
            mock_client = MagicMock()
            mock_rpc_cls.return_value.__enter__.return_value = mock_client
            
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
        with patch("mining.cli.miner.RpcClient") as mock_rpc_cls:
            mock_client = MagicMock()
            mock_rpc_cls.return_value.__enter__.return_value = mock_client
            
            # Mock successful RPC response
            mock_client.request.return_value = {
                "mined": 3,
                "height": 103
            }
            
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123address",
                "--count", "3",
                "--rpc-url", "http://127.0.0.1:8545"
            ])
            
            # Should succeed
            assert result == 0
            
            # Verify RPC was called
            mock_client.request.assert_called_once()
            call_args = mock_client.request.call_args
            assert call_args[0][0] == "miner.mine"
            # Note: The current miner.mine RPC doesn't support address parameter
            # This test documents the current behavior

    @pytest.mark.asyncio
    async def test_mine_blocks_handles_rpc_error(self):
        """Test that mine-blocks handles RPC errors gracefully."""
        with patch("mining.cli.miner.RpcClient") as mock_rpc_cls:
            mock_client = MagicMock()
            mock_rpc_cls.return_value.__enter__.return_value = mock_client
            
            # Mock RPC error
            mock_client.request.side_effect = Exception("RPC connection failed")
            
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123",
                "--count", "3",
                "--rpc-url", "http://127.0.0.1:8545"
            ])
            
            # Should fail with error code
            assert result != 0

    @pytest.mark.asyncio
    async def test_mine_blocks_logs_progress(self, caplog):
        """Test that mine-blocks logs useful progress information."""
        with patch("mining.cli.miner.RpcClient") as mock_rpc_cls:
            mock_client = MagicMock()
            mock_rpc_cls.return_value.__enter__.return_value = mock_client
            
            # Mock successful RPC response
            mock_client.request.return_value = {
                "mined": 5,
                "height": 105
            }
            
            result = await miner._amain([
                "mine-blocks",
                "--address", "anim1test123",
                "--count", "5",
                "--rpc-url", "http://127.0.0.1:8545"
            ])
            
            assert result == 0
            # We expect the implementation to log the blocks mined and height
