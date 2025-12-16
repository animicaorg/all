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


def test_mining_default_behavior_documented() -> None:
    """Test that mining CLI option defaults are documented correctly.
    
    This is a documentation test that verifies the mining CLI's use_proxy
    parameter has the correct default value to ensure P2P-first behavior.
    """
    # Verify the default is False (no proxy) via inspection
    # This test documents the expectation without needing full CLI dependencies
    
    # The mining.py file should have use_proxy default=False
    import os
    mining_file = os.path.join(os.path.dirname(__file__), "../../../python/animica/cli/mining.py")
    
    if os.path.exists(mining_file):
        with open(mining_file, "r") as f:
            content = f.read()
            
        # Verify use_proxy is defined with False default
        assert "use_proxy: bool = typer.Option(\n        False," in content, \
            "use_proxy should default to False for P2P-first behavior"
        
        # Verify DEPRECATED warning exists
        assert "DEPRECATED" in content, \
            "Proxy usage should be marked as deprecated"
        
        # Verify P2P messaging exists
        assert "P2P" in content, \
            "Documentation should mention P2P-first approach"
