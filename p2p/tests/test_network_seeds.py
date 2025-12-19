"""
Test network-specific seed configuration and fallback behavior.
"""
import os
import pytest

from p2p import config as p2p_config
from p2p.discovery import seeds as seed_discovery


MAINNET_SEED_HOSTS = {"rpc.animica.org", "mainnet.animica.org"}
TESTNET_SEED_HOSTS = {"rpc.testnet.animica.org", "testnet.animica.org"}
DEVNET_SEED_HOSTS = {"devnet.animica.org"}
FALLBACK_SEED_IP = "144.126.133.21"


class TestNetworkSpecificSeeds:
    """Test that seeds are correctly selected based on chain_id/network."""

    def test_default_seeds_by_network_mainnet(self):
        """Test mainnet (chain_id=1) seeds point at the primary host."""
        seeds = p2p_config.DEFAULT_SEEDS_BY_NETWORK[1]

        for host in MAINNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)
        # Should include both QUIC and TCP
        assert any("quic" in s for s in seeds)
        assert any("tcp/30333" in s for s in seeds)

    def test_default_seeds_by_network_testnet(self):
        """Test testnet (chain_id=2) seeds point at the primary host."""
        seeds = p2p_config.DEFAULT_SEEDS_BY_NETWORK[2]

        for host in TESTNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)
        # Should include both QUIC and TCP
        assert any("quic" in s for s in seeds)
        assert any("tcp/30333" in s for s in seeds)

    def test_default_seeds_by_network_devnet(self):
        """Test devnet (chain_id=1337) seeds point at the primary host."""
        seeds = p2p_config.DEFAULT_SEEDS_BY_NETWORK[1337]

        for host in DEVNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)
        # Should include both QUIC and TCP
        assert any("quic" in s for s in seeds)
        assert any("tcp/30333" in s for s in seeds)

    def test_network_name_to_chain_id_mapping(self):
        """Test network name to chain_id mapping."""
        assert p2p_config.NETWORK_NAME_TO_CHAIN_ID["mainnet"] == 1
        assert p2p_config.NETWORK_NAME_TO_CHAIN_ID["testnet"] == 2
        assert p2p_config.NETWORK_NAME_TO_CHAIN_ID["devnet"] == 1337


class TestSeedLoadingFromEnv:
    """Test seed loading with various environment variable configurations."""

    def test_load_seeds_with_chain_id_mainnet(self, monkeypatch):
        """Test loading seeds for mainnet via chain_id parameter."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_NETWORK", raising=False)
        
        seeds = p2p_config._load_seeds_from_env(chain_id=1)

        for host in MAINNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)

    def test_load_seeds_with_chain_id_testnet(self, monkeypatch):
        """Test loading seeds for testnet via chain_id parameter."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_NETWORK", raising=False)
        
        seeds = p2p_config._load_seeds_from_env(chain_id=2)

        for host in TESTNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)

    def test_load_seeds_with_network_env_mainnet(self, monkeypatch):
        """Test loading seeds via ANIMICA_P2P_NETWORK=mainnet."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.setenv("ANIMICA_P2P_NETWORK", "mainnet")

        seeds = p2p_config._load_seeds_from_env()

        for host in MAINNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)

    def test_load_seeds_with_network_env_testnet(self, monkeypatch):
        """Test loading seeds via ANIMICA_P2P_NETWORK=testnet."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.setenv("ANIMICA_P2P_NETWORK", "testnet")
        
        seeds = p2p_config._load_seeds_from_env()

        for host in TESTNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)

    def test_load_seeds_with_global_network_env(self, monkeypatch):
        """Test loading seeds via ANIMICA_NETWORK fallback."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_NETWORK", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_CHAIN_ID", raising=False)
        monkeypatch.setenv("ANIMICA_NETWORK", "testnet")

        seeds = p2p_config._load_seeds_from_env()

        # Should return testnet seeds through global network env
        for host in TESTNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)

    def test_load_seeds_with_global_chain_id_env(self, monkeypatch):
        """Test loading seeds via ANIMICA_CHAIN_ID fallback."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_NETWORK", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_CHAIN_ID", raising=False)
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        monkeypatch.setenv("ANIMICA_CHAIN_ID", "2")

        seeds = p2p_config._load_seeds_from_env()

        # Should return testnet seeds based on global chain id env
        for host in TESTNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)

    def test_explicit_seeds_override_defaults(self, monkeypatch):
        """Test that ANIMICA_P2P_SEEDS overrides network-specific defaults."""
        custom_seeds = "/ip4/1.2.3.4/tcp/1234,/ip4/5.6.7.8/tcp/5678"
        monkeypatch.setenv("ANIMICA_P2P_SEEDS", custom_seeds)
        monkeypatch.setenv("ANIMICA_P2P_NETWORK", "mainnet")
        
        seeds = p2p_config._load_seeds_from_env(chain_id=1)
        
        # Should use custom seeds, not network defaults
        assert "/ip4/1.2.3.4/tcp/1234" in seeds
        assert "/ip4/5.6.7.8/tcp/5678" in seeds
        assert not any("144.126.133.21" in s for s in seeds)

    def test_network_env_case_insensitive(self, monkeypatch):
        """Test that network name is case-insensitive."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.setenv("ANIMICA_P2P_NETWORK", "MAINNET")

        seeds = p2p_config._load_seeds_from_env()

        # Should still work with uppercase
        for host in MAINNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)

    def test_fallback_to_mainnet_default(self, monkeypatch):
        """Test fallback to mainnet DEFAULT_SEEDS when no chain_id or network."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_NETWORK", raising=False)
        
        seeds = p2p_config._load_seeds_from_env(chain_id=None)

        # Should return mainnet default seeds
        assert seeds == p2p_config.DEFAULT_SEEDS
        for host in MAINNET_SEED_HOSTS:
            assert any(host in s for s in seeds)
        assert any(FALLBACK_SEED_IP in s for s in seeds)


class TestEmbeddedFallbackSeeds:
    """Test embedded fallback seeds in discovery module."""

    def test_embedded_fallback_seeds_defined(self):
        """Test that embedded fallback seeds are defined."""
        assert len(seed_discovery.EMBEDDED_FALLBACK_SEEDS) > 0
        
        # Should include both DNS host and IP fallback
        assert any(
            host in s
            for host in MAINNET_SEED_HOSTS | TESTNET_SEED_HOSTS | DEVNET_SEED_HOSTS
            for s in seed_discovery.EMBEDDED_FALLBACK_SEEDS
        )
        assert any(FALLBACK_SEED_IP in s for s in seed_discovery.EMBEDDED_FALLBACK_SEEDS)

    def test_network_dns_seeds_mapping(self):
        """Test that network DNS seeds are defined for all networks."""
        assert 1 in seed_discovery.NETWORK_DNS_SEEDS  # mainnet
        assert 2 in seed_discovery.NETWORK_DNS_SEEDS  # testnet
        assert 1337 in seed_discovery.NETWORK_DNS_SEEDS  # devnet

    def test_network_https_seeds_mapping(self):
        """Test that network HTTPS seeds are defined for all networks."""
        assert 1 in seed_discovery.NETWORK_HTTPS_SEEDS  # mainnet
        assert 2 in seed_discovery.NETWORK_HTTPS_SEEDS  # testnet
        assert 1337 in seed_discovery.NETWORK_HTTPS_SEEDS  # devnet

    def test_discover_from_static_includes_fallbacks(self):
        """Test that static discovery works with fallback seeds."""
        bundle = seed_discovery.discover_from_static(seed_discovery.EMBEDDED_FALLBACK_SEEDS)
        
        assert len(bundle.endpoints) > 0
        # Should include both DNS host and IP
        assert any(
            host in ep.host
            for host in MAINNET_SEED_HOSTS | TESTNET_SEED_HOSTS | DEVNET_SEED_HOSTS
            for ep in bundle.endpoints
        )
        assert any(FALLBACK_SEED_IP in ep.host for ep in bundle.endpoints)


class TestFullConfigLoad:
    """Test the full config loading with chain_id."""

    def test_load_config_with_chain_id_env(self, monkeypatch):
        """Test that load_config respects ANIMICA_P2P_CHAIN_ID."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.setenv("ANIMICA_P2P_CHAIN_ID", "1")
        
        cfg = p2p_config.load_config()

        # Should include mainnet seeds pointed at DNS + IP fallback
        for host in MAINNET_SEED_HOSTS:
            assert any(host in s for s in cfg.seeds)
        assert any(FALLBACK_SEED_IP in s for s in cfg.seeds)

    def test_load_config_with_global_chain_id_env(self, monkeypatch):
        """Test that load_config respects global ANIMICA_CHAIN_ID when P2P env not set."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_CHAIN_ID", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_NETWORK", raising=False)
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        monkeypatch.setenv("ANIMICA_CHAIN_ID", "2")

        cfg = p2p_config.load_config()

        # Should include testnet seeds via global chain id
        for host in TESTNET_SEED_HOSTS:
            assert any(host in s for s in cfg.seeds)
        assert any(FALLBACK_SEED_IP in s for s in cfg.seeds)

    def test_load_config_with_global_network_env(self, monkeypatch):
        """Test that load_config respects global ANIMICA_NETWORK."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_CHAIN_ID", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_NETWORK", raising=False)
        monkeypatch.setenv("ANIMICA_NETWORK", "devnet")

        cfg = p2p_config.load_config()

        # Should include devnet seeds from global network env
        for host in DEVNET_SEED_HOSTS:
            assert any(host in s for s in cfg.seeds)
        assert any(FALLBACK_SEED_IP in s for s in cfg.seeds)

    def test_load_config_with_network_env(self, monkeypatch):
        """Test that load_config respects ANIMICA_P2P_NETWORK."""
        monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
        monkeypatch.delenv("ANIMICA_P2P_CHAIN_ID", raising=False)
        monkeypatch.setenv("ANIMICA_P2P_NETWORK", "testnet")
        
        cfg = p2p_config.load_config()

        # Should include testnet seeds
        for host in TESTNET_SEED_HOSTS:
            assert any(host in s for s in cfg.seeds)
        assert any(FALLBACK_SEED_IP in s for s in cfg.seeds)
