"""
Test suite for mainnet chain_id=0 genesis identity and verification.

Validates:
1. Mainnet genesis file has correct chain_id=0
2. Pinned genesis hash matches the actual file
3. Network identity is consistent across all layers
"""

import pytest
from pathlib import Path


def test_mainnet_genesis_file_exists():
    """Test that mainnet.json exists at the expected location."""
    from core.network_params import GENESIS_DIR
    
    mainnet_genesis = GENESIS_DIR / "mainnet.json"
    assert mainnet_genesis.exists(), f"Mainnet genesis file not found at {mainnet_genesis}"


def test_mainnet_genesis_has_chain_id_0():
    """Test that mainnet.json explicitly declares chainId=0."""
    from core.network_params import GENESIS_DIR
    import json
    
    mainnet_genesis = GENESIS_DIR / "mainnet.json"
    with open(mainnet_genesis) as f:
        genesis_data = json.load(f)
    
    chain_id = genesis_data.get("chainId")
    assert chain_id == 0, (
        f"Mainnet genesis file should have chainId=0, but got chainId={chain_id}. "
        f"File: {mainnet_genesis}"
    )


def test_mainnet_pinned_hash_matches_file():
    """Test that the pinned mainnet genesis hash matches the computed hash from file."""
    from core.network_params import get_pinned_genesis_hash, GENESIS_DIR
    from core.network_manifest import compute_genesis_hash
    
    # Get pinned hash for mainnet (chain_id=0)
    pinned_hash = get_pinned_genesis_hash(chain_id=0)
    assert pinned_hash is not None, "Mainnet (chain_id=0) should have a pinned genesis hash"
    
    # Compute hash from actual file
    mainnet_genesis = GENESIS_DIR / "mainnet.json"
    computed_hash = compute_genesis_hash(mainnet_genesis)
    
    assert computed_hash == pinned_hash, (
        f"Genesis hash mismatch for mainnet (chain_id=0)!\n"
        f"  Pinned:   0x{pinned_hash.hex()}\n"
        f"  Computed: 0x{computed_hash.hex()}\n"
        f"  File:     {mainnet_genesis}\n"
        f"This means the genesis file was modified without updating the pinned hash constant.\n"
        f"To fix: Update MAINNET_GENESIS_HASH_HEX in core/network_params.py"
    )


def test_mainnet_manifest_consistency():
    """Test that mainnet manifest is internally consistent."""
    from core.network_manifest import MAINNET_MANIFEST, verify_genesis
    
    # Check basic fields
    assert MAINNET_MANIFEST.chain_id == 0, "Mainnet manifest should have chain_id=0"
    assert MAINNET_MANIFEST.network_name == "mainnet", "Mainnet manifest should have network_name='mainnet'"
    assert MAINNET_MANIFEST.genesis_path.exists(), f"Genesis file not found: {MAINNET_MANIFEST.genesis_path}"
    
    # Verify genesis hash matches
    is_valid = verify_genesis(MAINNET_MANIFEST, raise_on_mismatch=False)
    assert is_valid, (
        f"Mainnet manifest genesis verification failed!\n"
        f"  Pinned:       {MAINNET_MANIFEST.pinned_genesis_hash_hex}\n"
        f"  Genesis path: {MAINNET_MANIFEST.genesis_path}\n"
        f"Run: animica chain genesis verify --network mainnet"
    )


def test_network_params_mainnet_mapping():
    """Test that network_params correctly maps mainnet to chain_id=0."""
    from core.network_params import get_network_params, get_pinned_genesis_hash, get_network_genesis_path
    
    # Test by network name
    params_by_name = get_network_params(network_name="mainnet")
    assert params_by_name is not None, "Could not resolve mainnet params by name"
    assert params_by_name.chain_id == 0, f"Mainnet params should have chain_id=0, got {params_by_name.chain_id}"
    
    # Test by chain_id
    params_by_id = get_network_params(chain_id=0)
    assert params_by_id is not None, "Could not resolve chain_id=0 params"
    assert params_by_id.name == "mainnet", f"Chain ID 0 should map to mainnet, got {params_by_id.name}"
    
    # Test genesis path
    genesis_path = get_network_genesis_path(chain_id=0)
    assert genesis_path is not None, "Could not get genesis path for chain_id=0"
    assert genesis_path.exists(), f"Genesis path does not exist: {genesis_path}"
    assert "mainnet" in str(genesis_path).lower(), f"Genesis path should contain 'mainnet': {genesis_path}"
    
    # Test pinned hash
    pinned = get_pinned_genesis_hash(chain_id=0)
    assert pinned is not None, "Could not get pinned hash for chain_id=0"
    assert len(pinned) == 32, f"Genesis hash should be 32 bytes, got {len(pinned)}"


def test_config_mainnet_validation():
    """Test that config.py enforces mainnet=chain_id=0."""
    from animica.config import load_network_config
    import os
    
    # Save original env
    orig_network = os.environ.get("ANIMICA_NETWORK")
    orig_chain_id = os.environ.get("ANIMICA_CHAIN_ID")
    
    try:
        # Test 1: mainnet should resolve to chain_id=0
        os.environ["ANIMICA_NETWORK"] = "mainnet"
        if "ANIMICA_CHAIN_ID" in os.environ:
            del os.environ["ANIMICA_CHAIN_ID"]
        
        config = load_network_config()
        assert config.chain_id == 0, f"Mainnet should have chain_id=0, got {config.chain_id}"
        assert config.name == "mainnet", f"Network name should be mainnet, got {config.name}"
        
        # Test 2: mainnet with explicit chain_id=1 should fail
        os.environ["ANIMICA_NETWORK"] = "mainnet"
        os.environ["ANIMICA_CHAIN_ID"] = "1"
        
        with pytest.raises(ValueError, match="mainnet.*MUST use chain_id=0"):
            load_network_config()
        
    finally:
        # Restore env
        if orig_network:
            os.environ["ANIMICA_NETWORK"] = orig_network
        elif "ANIMICA_NETWORK" in os.environ:
            del os.environ["ANIMICA_NETWORK"]
        
        if orig_chain_id:
            os.environ["ANIMICA_CHAIN_ID"] = orig_chain_id
        elif "ANIMICA_CHAIN_ID" in os.environ:
            del os.environ["ANIMICA_CHAIN_ID"]


def test_docker_compose_mainnet_config():
    """Test that docker-compose.mainnet.yml has correct environment variables."""
    import yaml
    from pathlib import Path
    
    compose_file = Path(__file__).parent.parent / "ops" / "docker" / "docker-compose.mainnet.yml"
    if not compose_file.exists():
        pytest.skip(f"Docker compose file not found: {compose_file}")
    
    with open(compose_file) as f:
        compose_data = yaml.safe_load(f)
    
    node_env = compose_data.get("services", {}).get("node", {}).get("environment", {})
    
    # Check chain_id default
    chain_id_value = node_env.get("ANIMICA_CHAIN_ID", "")
    # Extract default from ${VAR:-default} syntax
    if ":-" in chain_id_value:
        chain_id_default = chain_id_value.split(":-")[1].rstrip("}")
        assert chain_id_default == "0", (
            f"Docker compose mainnet should default to ANIMICA_CHAIN_ID=0, got {chain_id_default}"
        )
    
    # Check network
    network_value = node_env.get("ANIMICA_NETWORK", "")
    assert "mainnet" in network_value.lower(), (
        f"Docker compose mainnet should set ANIMICA_NETWORK=mainnet, got {network_value}"
    )
    
    # Check genesis path
    genesis_path = node_env.get("GENESIS_PATH", "")
    if ":-" in genesis_path:
        genesis_default = genesis_path.split(":-")[1].rstrip("}")
        assert "mainnet.json" in genesis_default, (
            f"Docker compose mainnet should use mainnet.json, got {genesis_default}"
        )
