"""Tests for mining CLI with proxy support."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock, MagicMock, patch

import typer
from animica.cli import mining
from typer.testing import CliRunner

runner = CliRunner()


def test_mine_blocks_with_proxy_enabled(monkeypatch: Any) -> None:
    """Test that mine-blocks can use proxy when explicitly enabled with URL."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    # Set proxy URL to enable proxy
    monkeypatch.setenv("ANIMICA_TRUSTED_RPC_URL", "http://127.0.0.1:8545/rpc")
    
    # Mock proxy
    mock_proxy = Mock()
    mock_proxy.config = Mock()
    mock_proxy.config.trusted_rpc_url = "http://127.0.0.1:8545/rpc"
    mock_proxy.config.max_retries = 3
    mock_proxy.config.retry_delay_ms = 1000
    mock_proxy.config.timeout_seconds = 30.0
    mock_proxy.sync_forward_request = Mock(return_value={"mined": 1, "height": 100, "totalReward": 5000000000})
    
    # Mock RPC client
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            # Should not be called directly when proxy is enabled
            raise AssertionError("Direct RPC call should not happen with proxy enabled")
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    # Mock proxy module
    mock_proxy_module = Mock()
    mock_proxy_module.create_proxy = Mock(return_value=mock_proxy)
    mock_proxy_module.ProxyConfig = Mock()
    monkeypatch.setitem(__import__("sys").modules, "rpc.proxy", mock_proxy_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
            "--use-proxy",  # Explicitly enable proxy
        ],
    )
    
    # Verify proxy was used
    assert mock_proxy.sync_forward_request.called
    assert result.exit_code == 0
    assert "DEPRECATED" in result.output or "Proxy mode" in result.output
    assert "Successfully mined" in result.output


def test_mine_blocks_with_proxy_disabled(monkeypatch: Any) -> None:
    """Test that mine-blocks uses direct RPC by default (proxy disabled)."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    
    request_called = {"count": 0}
    
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            request_called["count"] += 1
            return {"mined": 1, "height": 100, "totalReward": 5000000000}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
            # No --use-proxy flag - proxy disabled by default
        ],
    )
    
    # Verify direct RPC was used (not proxy)
    assert request_called["count"] > 0
    assert result.exit_code == 0
    assert "DEPRECATED" not in result.output  # No proxy warning
    assert "P2P" in result.output or "local" in result.output
    assert "Successfully mined" in result.output


def test_mine_blocks_proxy_with_fallback(monkeypatch: Any) -> None:
    """Test that proxy falls back to local node on failure when explicitly enabled."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    # Set proxy URL to enable proxy
    monkeypatch.setenv("ANIMICA_TRUSTED_RPC_URL", "http://127.0.0.1:8545/rpc")
    
    fallback_called = {"count": 0}
    
    # Mock proxy that calls fallback
    def mock_sync_forward(method, params, fallback_handler=None):
        # Simulate proxy failure, invoke fallback
        if fallback_handler:
            fallback_called["count"] += 1
            return fallback_handler()
        raise Exception("Proxy failed and no fallback provided")
    
    mock_proxy = Mock()
    mock_proxy.config = Mock()
    mock_proxy.config.trusted_rpc_url = "http://127.0.0.1:8545/rpc"
    mock_proxy.config.max_retries = 3
    mock_proxy.config.retry_delay_ms = 1000
    mock_proxy.config.timeout_seconds = 30.0
    mock_proxy.sync_forward_request = mock_sync_forward
    
    # Mock RPC client for fallback
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            return {"mined": 1, "height": 100, "totalReward": 5000000000}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    # Mock proxy module
    mock_proxy_module = Mock()
    mock_proxy_module.create_proxy = Mock(return_value=mock_proxy)
    mock_proxy_module.ProxyConfig = Mock()
    monkeypatch.setitem(__import__("sys").modules, "rpc.proxy", mock_proxy_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
            "--use-proxy",  # Explicitly enable proxy
        ],
    )
    
    # Verify fallback was used
    assert fallback_called["count"] > 0
    assert result.exit_code == 0
    assert "Successfully mined" in result.output


def test_mine_blocks_proxy_verbose_output(monkeypatch: Any) -> None:
    """Test verbose output with proxy explicitly enabled."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    # Set proxy URL to enable proxy
    monkeypatch.setenv("ANIMICA_TRUSTED_RPC_URL", "http://127.0.0.1:8545/rpc")
    
    # Mock proxy
    mock_proxy = Mock()
    mock_proxy.config = Mock()
    mock_proxy.config.trusted_rpc_url = "http://127.0.0.1:8545/rpc"
    mock_proxy.config.max_retries = 3
    mock_proxy.config.retry_delay_ms = 1000
    mock_proxy.config.timeout_seconds = 30.0
    mock_proxy.sync_forward_request = Mock(return_value={"mined": 1, "height": 100, "totalReward": 5000000000})
    
    # Mock RPC client
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            return {"transactions": []}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    # Mock proxy module
    mock_proxy_module = Mock()
    mock_proxy_module.create_proxy = Mock(return_value=mock_proxy)
    mock_proxy_module.ProxyConfig = Mock()
    monkeypatch.setitem(__import__("sys").modules, "rpc.proxy", mock_proxy_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
            "--use-proxy",  # Explicitly enable proxy
            "--verbose",
        ],
    )
    
    assert result.exit_code == 0
    assert "DEPRECATED" in result.output or "Proxy mode" in result.output
    assert "Max retries:" in result.output or "Retry delay:" in result.output
    assert "Successfully mined" in result.output


def test_mine_blocks_proxy_import_failure(monkeypatch: Any) -> None:
    """Test error when proxy is requested but module cannot be imported."""
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    monkeypatch.setattr(mining, "_validate_bech32_address", lambda x: True if x == test_address else False)
    # Set proxy URL to enable proxy
    monkeypatch.setenv("ANIMICA_TRUSTED_RPC_URL", "http://127.0.0.1:8545/rpc")
    
    # Mock RPC client
    class MockRpcClient:
        def __init__(self, *args, **kwargs):
            pass
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def request(self, method: str, params: Any):
            return {"mined": 1, "height": 100, "totalReward": 5000000000}
    
    mock_module = Mock()
    mock_module.RpcClient = MockRpcClient
    
    monkeypatch.setitem(__import__("sys").modules, "omni_sdk.rpc.http", mock_module)
    monkeypatch.setitem(__import__("sys").modules, "sdk.python.omni_sdk.rpc.http", mock_module)
    
    # Simulate import failure by making create_proxy raise ImportError
    import sys
    
    # Save original module if it exists
    original_proxy_module = sys.modules.get("rpc.proxy")
    
    # Create a mock that raises ImportError on create_proxy
    mock_proxy_module = Mock()
    mock_proxy_module.create_proxy = Mock(side_effect=ImportError("Proxy module not available"))
    mock_proxy_module.ProxyConfig = Mock()
    monkeypatch.setitem(sys.modules, "rpc.proxy", mock_proxy_module)
    
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "--address", test_address,
            "--count", "1",
            "--rpc-url", "http://127.0.0.1:8545",
            "--use-proxy",  # Explicitly request proxy
        ],
    )
    
    # Restore original module
    if original_proxy_module:
        sys.modules["rpc.proxy"] = original_proxy_module
    
    # Should warn and fall back to direct mining
    assert result.exit_code == 0 or result.exit_code == 1  # May fail if proxy required
    assert "Could not load proxy module" in result.output or "directly" in result.output
