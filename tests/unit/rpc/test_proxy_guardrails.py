"""Guardrail tests to ensure RPC proxy is disabled by default.

These tests enforce that:
1. The proxy is not used by default (no trusted_rpc_url set)
2. rpc.animica.org is never accessed unless explicitly enabled
3. Node consensus, mining, and sync do not depend on the proxy

This prevents accidental centralization and ensures P2P-first operation.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from rpc.proxy import ProxyConfig, RpcProxy


def test_proxy_config_no_default_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that ProxyConfig has no default trusted_rpc_url."""
    # Clear any existing env vars
    monkeypatch.delenv("ANIMICA_TRUSTED_RPC_URL", raising=False)
    
    config = ProxyConfig.from_env()
    
    # Should be None by default (no default URL)
    assert config.trusted_rpc_url is None, (
        "ProxyConfig.trusted_rpc_url must be None by default. "
        "Setting a default would enable centralized trust mode."
    )


def test_proxy_init_fails_without_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that RpcProxy initialization fails when trusted_rpc_url is not set."""
    # Clear any existing env vars
    monkeypatch.delenv("ANIMICA_TRUSTED_RPC_URL", raising=False)
    
    config = ProxyConfig.from_env()
    
    # Should raise ValueError when trusted_rpc_url is None
    with pytest.raises(ValueError, match="RPC Proxy is disabled by default"):
        RpcProxy(config)


def test_proxy_requires_explicit_url() -> None:
    """Test that proxy requires explicit URL configuration."""
    config = ProxyConfig(trusted_rpc_url=None)
    
    with pytest.raises(ValueError, match="RPC Proxy is disabled by default"):
        RpcProxy(config)


def test_proxy_warns_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that enabling proxy logs a warning about client-only use."""
    monkeypatch.setenv("ANIMICA_TRUSTED_RPC_URL", "https://test.example.com")
    
    config = ProxyConfig.from_env()
    
    # Should log warning when proxy is enabled
    with patch("rpc.proxy.logger") as mock_logger:
        proxy = RpcProxy(config)
        
        # Verify warning was logged
        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "CLIENT-ONLY" in warning_msg
        assert "Do NOT use for node consensus, mining, or sync" in warning_msg


def test_proxy_not_imported_by_default_in_rpc_server() -> None:
    """Test that rpc.server does not import proxy by default."""
    # This test ensures rpc.server doesn't have hardcoded proxy imports
    # We check the source code rather than importing (which requires fastapi)
    from pathlib import Path
    
    # Use Path for more robust file location
    test_file = Path(__file__).resolve()
    repo_root = test_file.parents[3]
    server_path = repo_root / "rpc" / "server.py"
    
    if not server_path.exists():
        pytest.skip(f"Server file not found at {server_path}")
    
    source = server_path.read_text()
    
    # Proxy should not be imported in server module
    assert "from rpc.proxy import" not in source, (
        "rpc.server should not import proxy module directly"
    )
    assert "import rpc.proxy" not in source, (
        "rpc.server should not import proxy module directly"
    )


def test_proxy_not_used_in_mining_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that mining CLI does not use proxy by default."""
    # This is a structural test - check source code for use_proxy default
    # We check the source rather than importing (which requires typer)
    from pathlib import Path
    import re
    
    # Use Path for more robust file location
    test_file = Path(__file__).resolve()
    repo_root = test_file.parents[3]
    mining_path = repo_root / "python" / "animica" / "cli" / "mining.py"
    
    if not mining_path.exists():
        pytest.skip(f"Mining file not found at {mining_path}")
    
    source = mining_path.read_text()
    
    # Look for use_proxy option definition
    # Should find "use_proxy: bool = typer.Option(" followed by "False"
    # Note: This regex is intentionally flexible to handle code formatting
    pattern = r'use_proxy:\s*bool\s*=\s*typer\.Option\s*\(\s*False'
    
    assert re.search(pattern, source), (
        "mine_blocks use_proxy parameter must default to False for P2P-first operation. "
        "Expected pattern: 'use_proxy: bool = typer.Option(False, ...)'\n"
        "Note: If code is refactored, this test may need updating to match new structure."
    )


def test_rpc_animica_org_blocked_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that rpc.animica.org cannot be accessed by default."""
    # Clear proxy URL
    monkeypatch.delenv("ANIMICA_TRUSTED_RPC_URL", raising=False)
    
    # Attempt to create proxy should fail
    config = ProxyConfig.from_env()
    
    with pytest.raises(ValueError):
        RpcProxy(config)
    
    # Verify the URL was not set to rpc.animica.org
    assert config.trusted_rpc_url is None
    assert config.trusted_rpc_url != "https://rpc.animica.org/rpc", (
        "rpc.animica.org must not be set as default trusted endpoint"
    )


def test_p2p_bootstrap_seeds_use_mainnet_animica_org() -> None:
    """Test that P2P seeds use mainnet.animica.org, not rpc.animica.org."""
    from p2p.config import DEFAULT_SEEDS_BY_NETWORK
    
    # Mainnet seeds should use mainnet.animica.org for P2P
    mainnet_seeds = DEFAULT_SEEDS_BY_NETWORK.get(1, ())
    
    # Should have mainnet.animica.org seeds
    assert any("mainnet.animica.org" in seed for seed in mainnet_seeds), (
        "Mainnet P2P seeds must include mainnet.animica.org"
    )
    
    # Should NOT have rpc.animica.org in P2P seeds
    assert not any("rpc.animica.org" in seed for seed in mainnet_seeds), (
        "rpc.animica.org is HTTP RPC only, not a P2P seed"
    )


def test_proxy_env_var_must_be_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that ANIMICA_TRUSTED_RPC_URL must be explicitly set to enable proxy."""
    # Test 1: No env var -> proxy disabled
    monkeypatch.delenv("ANIMICA_TRUSTED_RPC_URL", raising=False)
    config1 = ProxyConfig.from_env()
    assert config1.trusted_rpc_url is None
    
    # Test 2: Empty string env var -> proxy disabled
    monkeypatch.setenv("ANIMICA_TRUSTED_RPC_URL", "")
    config2 = ProxyConfig.from_env()
    assert config2.trusted_rpc_url == ""
    
    # Test 3: Explicit URL -> proxy enabled
    monkeypatch.setenv("ANIMICA_TRUSTED_RPC_URL", "https://custom.example.com")
    config3 = ProxyConfig.from_env()
    assert config3.trusted_rpc_url == "https://custom.example.com"


@pytest.mark.asyncio
async def test_rpc_deps_does_not_use_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that rpc.deps does not import or use proxy module."""
    # Clear proxy URL to ensure it's not accidentally used
    monkeypatch.delenv("ANIMICA_TRUSTED_RPC_URL", raising=False)
    
    # Import rpc.deps and verify it doesn't use proxy
    import rpc.deps
    
    # Check that proxy is not in the module's globals
    assert "proxy" not in dir(rpc.deps), (
        "rpc.deps should not import proxy module"
    )
    assert "RpcProxy" not in dir(rpc.deps), (
        "rpc.deps should not import RpcProxy"
    )


def test_default_config_promotes_p2p() -> None:
    """Test that default configuration promotes P2P over HTTP proxy."""
    # This is a policy test to ensure defaults align with P2P-first design
    from p2p.config import load_config
    
    # P2P should be enabled by default
    p2p_config = load_config()
    
    # At least one transport should be enabled
    assert (
        p2p_config.enable_tcp or 
        p2p_config.enable_quic or 
        p2p_config.enable_ws
    ), "P2P transports should be enabled by default"
    
    # Seeds should be configured for networks
    from p2p.config import DEFAULT_SEEDS_BY_NETWORK
    assert len(DEFAULT_SEEDS_BY_NETWORK) > 0, (
        "P2P seeds should be configured for known networks"
    )
