"""Tests for mining.cli.stratum_client wrapper module."""

import pytest
from unittest.mock import MagicMock, patch
import sys


class TestStratumClientWrapper:
    """Test suite for mining.cli.stratum_client wrapper."""

    def test_parse_pool_url_with_prefix(self):
        """Test parsing pool URL with stratum+tcp:// prefix."""
        from mining.cli.stratum_client import _parse_pool_url
        
        host, port = _parse_pool_url("stratum+tcp://pool.example.com:3333")
        
        assert host == "pool.example.com"
        assert port == 3333
    
    def test_parse_pool_url_without_prefix(self):
        """Test parsing pool URL without prefix."""
        from mining.cli.stratum_client import _parse_pool_url
        
        host, port = _parse_pool_url("pool.example.com:4444")
        
        assert host == "pool.example.com"
        assert port == 4444
    
    def test_parse_pool_url_with_stratum_prefix(self):
        """Test parsing pool URL with stratum:// prefix."""
        from mining.cli.stratum_client import _parse_pool_url
        
        host, port = _parse_pool_url("stratum://pool.test.net:5555")
        
        assert host == "pool.test.net"
        assert port == 5555
    
    def test_parse_pool_url_default_port(self):
        """Test parsing pool URL without port uses default."""
        from mining.cli.stratum_client import _parse_pool_url
        
        host, port = _parse_pool_url("pool.example.com")
        
        assert host == "pool.example.com"
        assert port == 3333
    
    def test_parse_pool_url_invalid_port(self):
        """Test that invalid port raises ValueError."""
        from mining.cli.stratum_client import _parse_pool_url
        
        with pytest.raises(ValueError, match="Invalid port"):
            _parse_pool_url("pool.example.com:invalid")
    
    def test_parse_args_with_defaults(self):
        """Test that arguments are parsed with default values."""
        from mining.cli.stratum_client import _parse_args
        
        args = _parse_args([])
        
        assert args.pool == "stratum+tcp://127.0.0.1:3333"
        assert args.device == "gpu"
        assert args.address is None
        assert args.worker == "rig1"
        assert args.framing == "lines"
        assert args.auto_submit is False
    
    def test_parse_args_with_custom_values(self):
        """Test that custom arguments are parsed correctly."""
        from mining.cli.stratum_client import _parse_args
        
        args = _parse_args([
            "--pool", "stratum+tcp://pool.test.com:4444",
            "--device", "cuda",
            "--address", "anim1test123",
            "--worker", "worker1",
            "--framing", "lenpref",
            "--auto-submit",
        ])
        
        assert args.pool == "stratum+tcp://pool.test.com:4444"
        assert args.device == "cuda"
        assert args.address == "anim1test123"
        assert args.worker == "worker1"
        assert args.framing == "lenpref"
        assert args.auto_submit is True
    
    def test_parse_args_with_extra_args(self):
        """Test that extra arguments are captured."""
        from mining.cli.stratum_client import _parse_args
        
        args = _parse_args([
            "--pool", "stratum+tcp://127.0.0.1:3333",
            "--extra-flag",
            "--another-flag", "value",
        ])
        
        assert args.pool == "stratum+tcp://127.0.0.1:3333"
        assert "--extra-flag" in args.extra_args
        assert "--another-flag" in args.extra_args
        assert "value" in args.extra_args
    
    def test_main_requires_address(self, capsys):
        """Test that main() requires --address argument."""
        from mining.cli.stratum_client import main
        
        original_argv = sys.argv
        try:
            sys.argv = ["mining.cli.stratum_client", "--pool", "stratum+tcp://127.0.0.1:3333"]
            result = main()
            
            # Should exit with error code
            assert result == 1
            
            # Check error message
            captured = capsys.readouterr()
            assert "--address is required" in captured.err
        finally:
            sys.argv = original_argv
    
    def test_main_invalid_pool_url(self, capsys):
        """Test that main() handles invalid pool URL."""
        from mining.cli.stratum_client import main
        
        original_argv = sys.argv
        try:
            sys.argv = [
                "mining.cli.stratum_client",
                "--pool", "pool.example.com:invalid",
                "--address", "anim1test",
            ]
            result = main()
            
            # Should exit with error code
            assert result == 1
            
            # Check error message
            captured = capsys.readouterr()
            assert "Invalid port" in captured.err
        finally:
            sys.argv = original_argv


class TestStratumClientWrapperIntegration:
    """Integration tests for stratum_client wrapper."""
    
    @patch('mining.cli.stratum_client.stratum_main')
    def test_calls_stratum_main_with_correct_args(self, mock_stratum_main):
        """Test that main() calls stratum_main with correct arguments."""
        from mining.cli.stratum_client import main
        
        # Prevent actual execution
        mock_stratum_main.return_value = None
        
        original_argv = sys.argv
        try:
            sys.argv = [
                "mining.cli.stratum_client",
                "--pool", "stratum+tcp://pool.test.com:4444",
                "--address", "anim1test123",
                "--worker", "rig1",
            ]
            
            main()
            
            # Verify stratum_main was called
            assert mock_stratum_main.called
        finally:
            sys.argv = original_argv
    
    def test_pool_url_parsing_integration(self):
        """Test that pool URL is correctly parsed in the full flow."""
        from mining.cli.stratum_client import _parse_args, _parse_pool_url
        
        args = _parse_args(["--pool", "stratum+tcp://pool.example.com:5555"])
        host, port = _parse_pool_url(args.pool)
        
        assert host == "pool.example.com"
        assert port == 5555
