"""Tests for RPC proxy module."""

from __future__ import annotations

import asyncio
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from rpc.proxy import (
    ProxyConfig,
    RpcProxy,
    ProxyConnectionError,
    ProxyTimeoutError,
    create_proxy,
)


@pytest.fixture
def proxy_config() -> ProxyConfig:
    """Create test proxy config."""
    return ProxyConfig(
        trusted_rpc_url="https://test.rpc.example.com",
        max_retries=3,
        retry_delay_ms=100,  # Fast retries for testing
        timeout_seconds=5.0,
        enable_caching=False,
    )


@pytest.fixture
def proxy(proxy_config: ProxyConfig) -> RpcProxy:
    """Create test proxy instance."""
    return RpcProxy(proxy_config)


@pytest.mark.asyncio
async def test_forward_request_success(proxy: RpcProxy) -> None:
    """Test successful request forwarding."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"height": 100, "hash": "0xabc"},
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client
        
        result = await proxy.forward_request("chain.getHead", [])
        
        assert result == {"height": 100, "hash": "0xabc"}
        mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_forward_request_with_params(proxy: RpcProxy) -> None:
    """Test request forwarding with parameters."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": 1000,
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client
        
        result = await proxy.forward_request(
            "account.getBalance",
            ["0x1234"]
        )
        
        assert result == 1000
        
        # Verify payload
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["method"] == "account.getBalance"
        assert payload["params"] == ["0x1234"]


@pytest.mark.asyncio
async def test_forward_request_retry_on_timeout(proxy: RpcProxy) -> None:
    """Test retry logic on timeout."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"success": True},
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        
        # Fail first two attempts, succeed on third
        mock_client.post = AsyncMock(
            side_effect=[
                asyncio.TimeoutError("Timeout 1"),
                asyncio.TimeoutError("Timeout 2"),
                mock_response,
            ]
        )
        mock_client_class.return_value = mock_client
        
        result = await proxy.forward_request("test.method", [])
        
        assert result == {"success": True}
        assert mock_client.post.call_count == 3


@pytest.mark.asyncio
async def test_forward_request_all_retries_fail(proxy: RpcProxy) -> None:
    """Test failure when all retries exhausted."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(
            side_effect=asyncio.TimeoutError("Timeout")
        )
        mock_client_class.return_value = mock_client
        
        with pytest.raises(ProxyTimeoutError):
            await proxy.forward_request("test.method", [])
        
        assert mock_client.post.call_count == 3  # max_retries


@pytest.mark.asyncio
async def test_forward_request_with_fallback(proxy: RpcProxy) -> None:
    """Test fallback handler invocation on failure."""
    fallback_result = {"fallback": True, "height": 50}
    
    async def fallback_handler():
        return fallback_result
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(
            side_effect=asyncio.TimeoutError("Timeout")
        )
        mock_client_class.return_value = mock_client
        
        result = await proxy.forward_request(
            "test.method",
            [],
            fallback_handler=fallback_handler,
        )
        
        assert result == fallback_result


@pytest.mark.asyncio
async def test_forward_request_rpc_error(proxy: RpcProxy) -> None:
    """Test handling of RPC error response."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32600,
            "message": "Invalid request",
        },
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client
        
        with pytest.raises(ProxyConnectionError):
            await proxy.forward_request("test.method", [])


@pytest.mark.asyncio
async def test_forward_request_http_error(proxy: RpcProxy) -> None:
    """Test handling of HTTP errors."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPError("Connection failed")
        )
        mock_client_class.return_value = mock_client
        
        with pytest.raises(ProxyConnectionError):
            await proxy.forward_request("test.method", [])


def test_sync_forward_request(proxy: RpcProxy) -> None:
    """Test synchronous wrapper for forward_request."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"sync": True},
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client
        
        result = proxy.sync_forward_request("test.method", [])
        
        assert result == {"sync": True}


def test_sync_forward_request_with_sync_fallback(proxy: RpcProxy) -> None:
    """Test sync fallback handler."""
    fallback_result = {"fallback": "sync"}
    
    def sync_fallback():
        return fallback_result
    
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(
            side_effect=asyncio.TimeoutError("Timeout")
        )
        mock_client_class.return_value = mock_client
        
        result = proxy.sync_forward_request(
            "test.method",
            [],
            fallback_handler=sync_fallback,
        )
        
        assert result == fallback_result


def test_proxy_config_from_env(monkeypatch: Any) -> None:
    """Test loading proxy config from environment."""
    monkeypatch.setenv("ANIMICA_TRUSTED_RPC_URL", "https://custom.rpc.example.com")
    monkeypatch.setenv("ANIMICA_PROXY_MAX_RETRIES", "5")
    monkeypatch.setenv("ANIMICA_PROXY_RETRY_DELAY_MS", "2000")
    monkeypatch.setenv("ANIMICA_PROXY_TIMEOUT_SECONDS", "60.0")
    monkeypatch.setenv("ANIMICA_PROXY_ENABLE_CACHE", "true")
    
    config = ProxyConfig.from_env()
    
    assert config.trusted_rpc_url == "https://custom.rpc.example.com"
    assert config.max_retries == 5
    assert config.retry_delay_ms == 2000
    assert config.timeout_seconds == 60.0
    assert config.enable_caching is True


def test_proxy_config_defaults(monkeypatch: Any) -> None:
    """Test default proxy config values."""
    # Clear any existing env vars
    monkeypatch.delenv("ANIMICA_TRUSTED_RPC_URL", raising=False)
    monkeypatch.delenv("ANIMICA_PROXY_MAX_RETRIES", raising=False)
    monkeypatch.delenv("ANIMICA_PROXY_RETRY_DELAY_MS", raising=False)
    monkeypatch.delenv("ANIMICA_PROXY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ANIMICA_PROXY_ENABLE_CACHE", raising=False)
    
    config = ProxyConfig.from_env()
    
    assert config.trusted_rpc_url == "https://rpc.animica.org/rpc"
    assert config.max_retries == 3
    assert config.retry_delay_ms == 1000
    assert config.timeout_seconds == 30.0
    assert config.enable_caching is False


def test_create_proxy_factory() -> None:
    """Test proxy factory function."""
    proxy = create_proxy()
    
    assert isinstance(proxy, RpcProxy)
    assert proxy.config.trusted_rpc_url == "https://rpc.animica.org/rpc"


def test_create_proxy_with_custom_config() -> None:
    """Test proxy factory with custom config."""
    config = ProxyConfig(
        trusted_rpc_url="https://custom.example.com",
        max_retries=5,
    )
    
    proxy = create_proxy(config)
    
    assert isinstance(proxy, RpcProxy)
    assert proxy.config.trusted_rpc_url == "https://custom.example.com"
    assert proxy.config.max_retries == 5
