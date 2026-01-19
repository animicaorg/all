"""
Unit tests for core.network_identity module.

Tests the single source of truth for network identity management.
"""

import os
import pytest
from pathlib import Path

from core.network_identity import (
    NetworkIdentity,
    normalize_network_name,
    get_chain_id_for_network,
    resolve_network_identity,
    validate_network_identity,
    NETWORK_CHAIN_ID_MAP,
)


class TestNetworkNormalization:
    """Test network name normalization."""
    
    def test_normalize_mainnet_variations(self):
        """Test that all mainnet variations normalize to 'mainnet'."""
        assert normalize_network_name("mainnet") == "mainnet"
        assert normalize_network_name("MAINNET") == "mainnet"
        assert normalize_network_name("main") == "mainnet"
        assert normalize_network_name("MAIN") == "mainnet"
        assert normalize_network_name("  mainnet  ") == "mainnet"
    
    def test_normalize_testnet_variations(self):
        """Test that all testnet variations normalize to 'testnet'."""
        assert normalize_network_name("testnet") == "testnet"
        assert normalize_network_name("TESTNET") == "testnet"
        assert normalize_network_name("test") == "testnet"
        assert normalize_network_name("TEST") == "testnet"
    
    def test_normalize_devnet_variations(self):
        """Test that all devnet variations normalize to 'devnet'."""
        assert normalize_network_name("devnet") == "devnet"
        assert normalize_network_name("DEVNET") == "devnet"
        assert normalize_network_name("dev") == "devnet"
        assert normalize_network_name("DEV") == "devnet"
    
    def test_normalize_unknown_network_raises(self):
        """Test that unknown network names raise ValueError."""
        with pytest.raises(ValueError, match="Unknown network"):
            normalize_network_name("unknown")
    
    def test_normalize_empty_defaults_to_mainnet(self):
        """Test that empty/None network defaults to mainnet."""
        assert normalize_network_name(None) == "mainnet"
        assert normalize_network_name("") == "mainnet"
        assert normalize_network_name("  ") == "mainnet"


class TestChainIdMapping:
    """Test network to chain_id mapping."""
    
    def test_mainnet_chain_id_is_zero(self):
        """Test that mainnet always maps to chain_id 0."""
        assert get_chain_id_for_network("mainnet") == 0
        assert NETWORK_CHAIN_ID_MAP["mainnet"] == 0
    
    def test_testnet_chain_id_is_two(self):
        """Test that testnet always maps to chain_id 2."""
        assert get_chain_id_for_network("testnet") == 2
        assert NETWORK_CHAIN_ID_MAP["testnet"] == 2
    
    def test_devnet_chain_id_is_1337(self):
        """Test that devnet always maps to chain_id 1337."""
        assert get_chain_id_for_network("devnet") == 1337
        assert NETWORK_CHAIN_ID_MAP["devnet"] == 1337
    
    def test_unknown_network_raises(self):
        """Test that unknown network raises ValueError."""
        with pytest.raises(ValueError, match="Unknown network"):
            get_chain_id_for_network("unknown")


class TestResolveNetworkIdentity:
    """Test network identity resolution."""
    
    def test_resolve_from_network_name(self):
        """Test resolving identity from network name only."""
        identity = resolve_network_identity(network="mainnet")
        assert identity.network == "mainnet"
        assert identity.chain_id == 0
        assert identity.genesis_path.exists()
        assert identity.genesis_identity_hash is not None
        assert len(identity.genesis_identity_hash) == 32
    
    def test_resolve_from_chain_id(self):
        """Test resolving identity from chain_id only."""
        identity = resolve_network_identity(chain_id=0)
        assert identity.network == "mainnet"
        assert identity.chain_id == 0
    
    def test_resolve_from_both_consistent(self):
        """Test resolving identity from both network and chain_id (consistent)."""
        identity = resolve_network_identity(network="mainnet", chain_id=0)
        assert identity.network == "mainnet"
        assert identity.chain_id == 0
    
    def test_resolve_from_both_inconsistent_raises(self):
        """Test that inconsistent network and chain_id raises ValueError."""
        with pytest.raises(ValueError, match="Chain ID mismatch"):
            resolve_network_identity(network="mainnet", chain_id=1)
        
        with pytest.raises(ValueError, match="Chain ID mismatch"):
            resolve_network_identity(network="testnet", chain_id=0)
    
    def test_resolve_defaults_to_mainnet(self):
        """Test that no arguments defaults to mainnet."""
        identity = resolve_network_identity()
        assert identity.network == "mainnet"
        assert identity.chain_id == 0
    
    def test_resolve_all_networks(self):
        """Test that all known networks resolve successfully."""
        for network, chain_id in NETWORK_CHAIN_ID_MAP.items():
            identity = resolve_network_identity(network=network)
            assert identity.network == network
            assert identity.chain_id == chain_id
            assert identity.genesis_path.exists()
            assert identity.genesis_identity_hash is not None
            assert identity.pinned_expected_hash is not None


class TestDeterminism:
    """Test that identity resolution is deterministic."""
    
    def test_same_inputs_same_hash(self):
        """Test that same inputs always produce same hash."""
        identity1 = resolve_network_identity(network="mainnet")
        identity2 = resolve_network_identity(network="mainnet")
        
        assert identity1.genesis_identity_hash == identity2.genesis_identity_hash
        assert identity1.pinned_expected_hash == identity2.pinned_expected_hash
    
    def test_hash_is_32_bytes(self):
        """Test that genesis hash is always 32 bytes."""
        for network in NETWORK_CHAIN_ID_MAP.keys():
            identity = resolve_network_identity(network=network)
            assert len(identity.genesis_identity_hash) == 32
            assert len(identity.pinned_expected_hash) == 32


class TestNetworkIdentityFields:
    """Test that all NetworkIdentity fields are populated correctly."""
    
    def test_all_fields_populated(self):
        """Test that all fields are populated for mainnet."""
        identity = resolve_network_identity(network="mainnet")
        
        assert identity.network == "mainnet"
        assert identity.chain_id == 0
        assert identity.genesis_path.exists()
        assert len(identity.genesis_json_canonical_bytes) > 0
        assert len(identity.genesis_identity_hash) == 32
        assert len(identity.pinned_expected_hash) == 32
        assert identity.db_dir.name == "chain-0"
        assert identity.p2p_dir.name == "p2p"
        assert identity.p2p_dir.parent == identity.db_dir
    
    def test_db_dir_reflects_chain_id(self):
        """Test that db_dir includes chain_id in path."""
        mainnet = resolve_network_identity(network="mainnet")
        assert "chain-0" in str(mainnet.db_dir)
        
        testnet = resolve_network_identity(network="testnet")
        assert "chain-2" in str(testnet.db_dir)
        
        devnet = resolve_network_identity(network="devnet")
        assert "chain-1337" in str(devnet.db_dir)


class TestMainnetChainIdZero:
    """Critical tests for mainnet chain_id=0 enforcement."""
    
    def test_mainnet_must_be_chain_id_zero(self):
        """Test the critical requirement: mainnet MUST be chain_id 0."""
        identity = resolve_network_identity(network="mainnet")
        assert identity.chain_id == 0, (
            "CRITICAL: mainnet MUST have chain_id=0. "
            "This is a non-negotiable requirement for network compatibility."
        )
    
    def test_chain_id_zero_must_be_mainnet(self):
        """Test that chain_id 0 always resolves to mainnet."""
        identity = resolve_network_identity(chain_id=0)
        assert identity.network == "mainnet", (
            "CRITICAL: chain_id=0 MUST resolve to mainnet. "
            "This is a non-negotiable requirement for network compatibility."
        )
    
    def test_mainnet_with_wrong_chain_id_raises(self):
        """Test that mainnet with chain_id != 0 raises clear error."""
        with pytest.raises(ValueError) as exc_info:
            resolve_network_identity(network="mainnet", chain_id=1)
        
        error_msg = str(exc_info.value)
        assert "mainnet" in error_msg.lower()
        assert "chain_id" in error_msg.lower()
        assert "0" in error_msg


class TestBuildTimeInvariant:
    """Build-time invariant test: mainnet genesis file must match pinned hash."""
    
    def test_mainnet_chain0_pinned_matches_file(self):
        """
        CRITICAL BUILD-TIME TEST: Mainnet genesis file must match pinned hash.
        
        This test ensures that the shipped genesis file for mainnet (chain_id=0)
        exactly matches the pinned hash in network_params.py.
        
        This prevents:
        - Accidental genesis modifications without updating pinned hash
        - Version skew between genesis file and pinned hash
        - Nodes starting with wrong genesis and failing to sync
        
        If this test fails:
        1. If genesis was intentionally changed, update MAINNET_GENESIS_HASH_HEX
           in core/network_params.py to match the new computed hash
        2. If genesis was NOT intentionally changed, revert the changes
        3. Never bypass this test in CI/production
        """
        identity = resolve_network_identity(network="mainnet", chain_id=0)
        
        # Load genesis file and compute hash
        computed_hash = identity.genesis_identity_hash
        pinned_hash = identity.pinned_expected_hash
        
        assert computed_hash == pinned_hash, (
            f"CRITICAL: Mainnet genesis file does not match pinned hash!\n"
            f"  Genesis file:      {identity.genesis_path}\n"
            f"  Computed hash:     0x{computed_hash.hex()}\n"
            f"  Pinned hash:       0x{pinned_hash.hex()}\n"
            f"\n"
            f"This is a build-time invariant that MUST be maintained.\n"
            f"If you intentionally changed genesis, update core/network_params.py:\n"
            f"  MAINNET_GENESIS_HASH_HEX = '0x{computed_hash.hex()}'\n"
            f"\n"
            f"If you did NOT intentionally change genesis, revert your changes.\n"
            f"This test prevents nodes from starting with incompatible genesis."
        )
