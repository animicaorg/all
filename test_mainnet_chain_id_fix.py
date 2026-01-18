"""
Test suite to verify mainnet chain_id=0 enforcement.

This test validates the critical fixes for the sync stall issue:
1. Mainnet genesis file has chain_id=0
2. Config validation enforces mainnet chain_id=0
3. RPC deps validation enforces mainnet chain_id=0
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add repo root to path for imports
repo_root = Path(__file__).resolve().parent
sys.path.insert(0, str(repo_root))


def test_mainnet_genesis_chain_id():
    """Test that mainnet.json has chain_id=0."""
    genesis_path = repo_root / "core" / "genesis" / "mainnet.json"
    assert genesis_path.exists(), f"Mainnet genesis not found at {genesis_path}"
    
    with open(genesis_path, "r") as f:
        genesis = json.load(f)
    
    chain_id = genesis.get("chainId")
    assert chain_id == 0, (
        f"Mainnet genesis MUST have chainId=0, but found chainId={chain_id}. "
        f"This causes peer identity mismatches and sync failures."
    )
    
    # Also check the description mentions chain_id=0
    description = genesis.get("meta", {}).get("description", "")
    assert "chainId=0" in description, (
        f"Mainnet genesis description should mention chainId=0 for clarity"
    )


def test_config_validates_mainnet_chain_id():
    """Test that config.py validates mainnet chain_id=0."""
    # Ensure repo root is in path for imports
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from animica.config import load_network_config
    
    # Test that mainnet loads with chain_id=0
    cfg = load_network_config("mainnet")
    assert cfg.chain_id == 0, (
        f"load_network_config('mainnet') should return chain_id=0, got {cfg.chain_id}"
    )
    
    # Test that explicitly setting wrong chain_id for mainnet raises error
    os.environ["ANIMICA_CHAIN_ID"] = "1"
    try:
        with pytest.raises(ValueError, match="mainnet.*MUST use chain_id=0"):
            load_network_config("mainnet")
    finally:
        # Clean up
        os.environ.pop("ANIMICA_CHAIN_ID", None)


def test_rpc_deps_validates_mainnet_chain_id():
    """Test that rpc.deps validates mainnet chain_id=0."""
    from dataclasses import dataclass
    from pathlib import Path
    
    # Ensure repo root is in path for imports
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from rpc.deps import build_context
    
    # Create a mock config with wrong chain_id for mainnet
    @dataclass
    class MockConfig:
        db_uri: str = "sqlite:///:memory:"
        chain_id: int = 1  # WRONG for mainnet
        genesis_path: Path = repo_root / "core" / "genesis" / "mainnet.json"
        log_level: str = "INFO"
        p2p_required: bool = False
        finality_depth: int = 12
    
    # Set environment to indicate mainnet
    os.environ["ANIMICA_NETWORK"] = "mainnet"
    try:
        with pytest.raises(ValueError, match="mainnet.*MUST use chain_id=0"):
            build_context(MockConfig())
    finally:
        os.environ.pop("ANIMICA_NETWORK", None)


def test_testnet_uses_chain_id_2():
    """Test that testnet uses chain_id=2 (for comparison)."""
    # Ensure repo root is in path for imports
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from animica.config import load_network_config
    
    cfg = load_network_config("testnet")
    assert cfg.chain_id == 2, (
        f"load_network_config('testnet') should return chain_id=2, got {cfg.chain_id}"
    )


def test_devnet_uses_chain_id_1337():
    """Test that devnet uses chain_id=1337 (for comparison)."""
    # Ensure repo root is in path for imports
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from animica.config import load_network_config
    
    cfg = load_network_config("devnet")
    assert cfg.chain_id == 1337, (
        f"load_network_config('devnet') should return chain_id=1337, got {cfg.chain_id}"
    )


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])
