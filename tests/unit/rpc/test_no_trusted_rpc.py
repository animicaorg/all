"""Tests to ensure mining/consensus does NOT call trusted RPC by default."""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock
import pytest


def test_proxy_disabled_by_default_no_url() -> None:
    """Test that proxy is disabled by default when URL not set."""
    from rpc.proxy import ProxyConfig
    
    # Clear environment
    with patch.dict(os.environ, {}, clear=True):
        config = ProxyConfig.from_env()
        assert config.trusted_rpc_url is None, "Proxy should have no URL by default"


def test_proxy_creation_fails_without_url() -> None:
    """Test that creating proxy without URL raises ValueError."""
    from rpc.proxy import RpcProxy, ProxyConfig
    
    config = ProxyConfig(trusted_rpc_url=None)
    
    with pytest.raises(ValueError, match="ANIMICA_TRUSTED_RPC_URL"):
        RpcProxy(config)


def test_proxy_no_network_call_when_disabled() -> None:
    """Test that no network calls are made when proxy is disabled."""
    import httpx
    
    # Mock httpx to fail if called
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.side_effect = AssertionError("httpx should not be called when proxy is disabled")
        
        # Try to create proxy without URL - should fail before making network call
        from rpc.proxy import create_proxy
        
        with pytest.raises(ValueError, match="ANIMICA_TRUSTED_RPC_URL"):
            create_proxy()
        
        # Verify no network call was attempted
        mock_client.assert_not_called()


def test_mining_cli_default_no_proxy() -> None:
    """Test that mining CLI does not use proxy by default."""
    from python.animica.cli import mining
    import typer
    from typer.testing import CliRunner
    
    runner = CliRunner()
    
    # Mock to prevent actual mining
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params):
            # Should be called directly (not via proxy)
            return {"mined": 1, "height": 100, "totalReward": 5000000000}
    
    with patch.dict(os.environ, {}, clear=True):
        with patch("python.animica.cli.mining._validate_bech32_address", return_value=True):
            with patch("python.animica.cli.mining.load_network_config") as mock_config:
                mock_config.return_value = MagicMock(rpc_url="http://localhost:8545", name="mainnet")
                
                # Mock RPC client module
                import sys
                mock_module = MagicMock()
                mock_module.RpcClient = MockRpcClient
                sys.modules["omni_sdk.rpc.http"] = mock_module
                sys.modules["sdk.python.omni_sdk.rpc.http"] = mock_module
                
                # Mock httpx to ensure no network calls
                with patch("httpx.AsyncClient") as mock_httpx:
                    mock_httpx.side_effect = AssertionError("Should not make external RPC calls by default")
                    
                    result = runner.invoke(
                        mining.app,
                        [
                            "mine-blocks",
                            "--address", test_address,
                            "--count", "1",
                        ],
                    )
                    
                    # Should succeed without calling external RPC
                    assert result.exit_code == 0
                    assert "Successfully mined" in result.output
                    assert "P2P" in result.output or "local" in result.output
                    # Verify httpx was never called
                    mock_httpx.assert_not_called()
