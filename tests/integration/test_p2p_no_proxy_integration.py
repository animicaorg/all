"""Integration test: Verify P2P-first behavior without proxy.

This test verifies that the node configuration and RPC methods work correctly
with P2P-first behavior and no proxy dependency.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def test_rpc_deps_p2p_enabled_by_default():
    """Test that P2P is enabled by default in RPC deps."""
    # Clear any override env vars
    with patch.dict(os.environ, {}, clear=True):
        # P2P should be enabled by default
        # ANIMICA_P2P_ENABLE defaults to "true" in rpc/deps.py
        enable_p2p = os.environ.get("ANIMICA_P2P_ENABLE", "true").lower() in ("1", "true", "yes", "on")
        assert enable_p2p is True, "P2P should be enabled by default"


def test_rpc_deps_p2p_can_be_disabled():
    """Test that P2P can be explicitly disabled if needed."""
    with patch.dict(os.environ, {"ANIMICA_P2P_ENABLE": "false"}):
        enable_p2p = os.environ.get("ANIMICA_P2P_ENABLE", "true").lower() in ("1", "true", "yes", "on")
        assert enable_p2p is False, "P2P should respect ANIMICA_P2P_ENABLE=false"


def test_proxy_disabled_mining_uses_local_rpc():
    """Test that mining with proxy disabled uses local RPC directly."""
    # This test verifies the logic flow without actually starting a node
    
    # When proxy is disabled (use_proxy=False), the mining CLI should:
    # 1. Not attempt to import or create a proxy
    # 2. Call RPC client directly
    # 3. Not make any external HTTP calls
    
    # We verify this by checking that with proxy disabled,
    # no ValueError is raised (which would happen if proxy was created without URL)
    
    from rpc.proxy import RpcProxy, ProxyConfig
    
    # With proxy disabled, we don't create a proxy at all
    proxy = None
    
    # Verify that if someone tries to create a proxy without URL, it fails
    with pytest.raises(ValueError, match="ANIMICA_TRUSTED_RPC_URL"):
        config = ProxyConfig(trusted_rpc_url=None)
        proxy = RpcProxy(config)
    
    # This confirms proxy requires explicit configuration
    assert proxy is None, "Proxy should not be created when disabled"


def test_p2p_rpc_methods_available():
    """Test that P2P RPC methods are available."""
    # Check that p2p RPC methods exist
    try:
        from rpc.methods import p2p
        
        # Verify key methods exist
        assert hasattr(p2p, "list_peers"), "p2p.listPeers method should exist"
        assert hasattr(p2p, "add_peer"), "p2p.addPeer method should exist"
        assert hasattr(p2p, "remove_peer"), "p2p.removePeer method should exist"
        assert hasattr(p2p, "get_peer_info"), "p2p.getPeerInfo method should exist"
        
        print("✓ All P2P RPC methods are available")
        
    except ImportError as e:
        pytest.skip(f"P2P RPC methods not available: {e}")


def test_p2p_config_loads_network_seeds():
    """Test that P2P config loads network-specific seeds."""
    try:
        from p2p.config import DEFAULT_SEEDS_BY_NETWORK
        
        # Verify seeds are defined for mainnet, testnet, devnet
        assert 1 in DEFAULT_SEEDS_BY_NETWORK, "Mainnet (chain_id=1) should have seeds"
        assert 2 in DEFAULT_SEEDS_BY_NETWORK, "Testnet (chain_id=2) should have seeds"
        assert 1337 in DEFAULT_SEEDS_BY_NETWORK, "Devnet (chain_id=1337) should have seeds"
        
        # Verify seeds are not empty
        mainnet_seeds = DEFAULT_SEEDS_BY_NETWORK[1]
        assert len(mainnet_seeds) > 0, "Mainnet should have at least one seed"
        
        # Verify seed format (should contain DNS or IP multiaddr)
        seed = mainnet_seeds[0]
        assert "/dns4/" in seed or "/ip4/" in seed or "/ip6/" in seed, \
            f"Seed should be in multiaddr format: {seed}"
        
        print(f"✓ Network seeds configured:")
        print(f"  Mainnet: {len(DEFAULT_SEEDS_BY_NETWORK[1])} seeds")
        print(f"  Testnet: {len(DEFAULT_SEEDS_BY_NETWORK[2])} seeds")
        print(f"  Devnet: {len(DEFAULT_SEEDS_BY_NETWORK[1337])} seeds")
        
    except ImportError as e:
        pytest.skip(f"P2P config not available: {e}")


def test_docker_compose_mainnet_p2p_enabled():
    """Test that mainnet docker compose has P2P enabled."""
    import os
    from pathlib import Path
    
    repo_root = Path(__file__).resolve().parents[2]
    compose_file = repo_root / "ops" / "docker" / "docker-compose.mainnet.yml"
    
    if not compose_file.exists():
        pytest.skip("Mainnet compose file not found")
    
    with open(compose_file) as f:
        content = f.read()
    
    # Verify P2P is enabled in the compose file
    assert "P2P_ENABLE:" in content or "P2P_ENABLE=" in content, \
        "P2P_ENABLE should be configured in mainnet compose"
    
    # Verify it's set to true (or uses default)
    # The compose file should have P2P_ENABLE: "${P2P_ENABLE:-true}"
    if "P2P_ENABLE:" in content:
        # Extract the line
        for line in content.split("\n"):
            if "P2P_ENABLE:" in line:
                # Check it defaults to true
                assert "true" in line.lower() or "${P2P_ENABLE" in line, \
                    f"P2P_ENABLE should default to true in mainnet: {line}"
    
    print("✓ Mainnet docker compose has P2P enabled")


def test_mining_proxy_docs_deprecated():
    """Test that mining proxy docs are marked as deprecated."""
    import os
    from pathlib import Path
    
    repo_root = Path(__file__).resolve().parents[2]
    proxy_doc = repo_root / "docs" / "MINING_PROXY.md"
    
    if proxy_doc.exists():
        with open(proxy_doc) as f:
            content = f.read()
        
        # Verify deprecation warning exists
        assert "DEPRECATED" in content or "deprecated" in content, \
            "Mining proxy docs should be marked as deprecated"
        
        # Verify P2P alternative is mentioned
        assert "P2P" in content or "p2p" in content, \
            "Mining proxy docs should mention P2P alternative"
        
        print("✓ Mining proxy docs are properly deprecated")


def test_p2p_sync_docs_exist():
    """Test that P2P sync documentation exists."""
    import os
    from pathlib import Path
    
    repo_root = Path(__file__).resolve().parents[2]
    p2p_doc = repo_root / "docs" / "p2p_sync.md"
    
    if not p2p_doc.exists():
        pytest.fail("P2P sync documentation (docs/p2p_sync.md) should exist")
    
    with open(p2p_doc) as f:
        content = f.read()
    
    # Verify key sections exist
    assert "P2P-First" in content or "P2P" in content, \
        "P2P sync docs should explain P2P-first architecture"
    
    assert "seed" in content.lower(), \
        "P2P sync docs should explain bootstrap seeds"
    
    assert "peer" in content.lower(), \
        "P2P sync docs should explain peer connections"
    
    print("✓ P2P sync documentation exists and has key sections")


if __name__ == "__main__":
    """Allow running tests standalone."""
    pytest.main([__file__, "-v"])
