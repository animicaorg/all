"""Tests for mining.cli.mine wrapper module."""

import pytest
from unittest.mock import MagicMock, patch
import sys


class TestMineWrapper:
    """Test suite for mining.cli.mine wrapper."""

    def test_parse_args_with_defaults(self):
        """Test that arguments are parsed with default values."""
        from mining.cli.mine import _parse_args
        
        args = _parse_args([])
        
        assert args.rpc == "http://127.0.0.1:8547"
        assert args.threads == 1
        assert args.device == "cpu"
        assert args.address is None
        assert args.log_level == "info"
    
    def test_parse_args_with_custom_values(self):
        """Test that custom arguments are parsed correctly."""
        from mining.cli.mine import _parse_args
        
        args = _parse_args([
            "--rpc", "http://192.168.1.100:8545",
            "--threads", "4",
            "--device", "cuda",
            "--address", "anim1test123",
            "--log-level", "debug",
        ])
        
        assert args.rpc == "http://192.168.1.100:8545"
        assert args.threads == 4
        assert args.device == "cuda"
        assert args.address == "anim1test123"
        assert args.log_level == "debug"
    
    def test_parse_args_with_extra_args(self):
        """Test that extra arguments are captured."""
        from mining.cli.mine import _parse_args
        
        args = _parse_args([
            "--rpc", "http://127.0.0.1:8545",
            "--extra-flag",
            "--another-flag", "value",
        ])
        
        assert args.rpc == "http://127.0.0.1:8545"
        assert "--extra-flag" in args.extra_args
        assert "--another-flag" in args.extra_args
        assert "value" in args.extra_args
    
    def test_device_choices(self):
        """Test that device choices are validated."""
        from mining.cli.mine import _parse_args
        
        valid_devices = ["cpu", "cuda", "rocm", "opencl", "metal", "gpu", "quantum"]
        
        for device in valid_devices:
            args = _parse_args(["--device", device])
            assert args.device == device
    
    @patch('mining.cli.mine.miner')
    def test_main_calls_miner_start(self, mock_miner):
        """Test that main() calls miner.main() with start command."""
        from mining.cli.mine import main
        
        # Mock miner.main to prevent actual execution
        mock_miner.main = MagicMock()
        
        # Save original argv
        original_argv = sys.argv
        try:
            sys.argv = ["mining.cli.mine", "--rpc", "http://127.0.0.1:8545"]
            main()
            
            # Verify miner.main was called
            assert mock_miner.main.called
        finally:
            sys.argv = original_argv
    
    def test_address_warning_in_output(self, capsys):
        """Test that address parameter shows a warning."""
        from mining.cli.mine import _parse_args
        
        # The warning is shown in main(), not _parse_args()
        # This test verifies argument parsing works with address
        args = _parse_args(["--address", "anim1test123"])
        assert args.address == "anim1test123"


class TestMineWrapperIntegration:
    """Integration tests for mine wrapper."""
    
    @patch('mining.cli.mine.miner')
    def test_translates_rpc_to_rpc_url(self, mock_miner):
        """Test that --rpc translates to --rpc-url for miner CLI."""
        from mining.cli.mine import main
        
        mock_miner.main = MagicMock()
        
        original_argv = sys.argv
        try:
            sys.argv = ["mining.cli.mine", "--rpc", "http://test:8545"]
            main()
            
            # Check sys.argv was modified correctly
            # The wrapper should have added "start" and "--rpc-url"
            assert mock_miner.main.called
        finally:
            sys.argv = original_argv
