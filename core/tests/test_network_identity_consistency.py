"""
Test for network identity consistency across the codebase.

This ensures that:
1. Mainnet chain_id is consistently defined as 0 everywhere
2. Genesis hash matches between manifest, params, and actual genesis file
3. Network manifest is available and valid
"""
from __future__ import annotations

import json
from pathlib import Path


def test_mainnet_chain_id_is_zero():
    """
    Verify that mainnet chain_id is consistently defined as 0.
    
    This is the canonical mainnet chain_id and must be consistent across:
    - core/network_manifest.py (MAINNET_MANIFEST.chain_id)
    - core/network_params.py (MAINNET_PARAMS.chain_id)
    - core/genesis/mainnet.json (chainId field)
    """
    from core.network_manifest import MAINNET_MANIFEST
    from core.network_params import MAINNET_PARAMS
    
    # Verify manifest
    assert MAINNET_MANIFEST.chain_id == 0, (
        f"MAINNET_MANIFEST.chain_id must be 0, got {MAINNET_MANIFEST.chain_id}"
    )
    
    # Verify network_params
    assert MAINNET_PARAMS.chain_id == 0, (
        f"MAINNET_PARAMS.chain_id must be 0, got {MAINNET_PARAMS.chain_id}"
    )
    
    # Verify genesis file
    genesis_path = Path(__file__).parent.parent.parent / "core" / "genesis" / "mainnet.json"
    assert genesis_path.exists(), f"Mainnet genesis file not found: {genesis_path}"
    
    genesis_data = json.loads(genesis_path.read_text())
    genesis_chain_id = genesis_data.get("chainId")
    assert genesis_chain_id == 0, (
        f"mainnet.json chainId must be 0, got {genesis_chain_id}"
    )
    
    print("✓ Mainnet chain_id is consistently 0 everywhere")


def test_mainnet_genesis_hash_consistency():
    """
    Verify that the pinned mainnet genesis hash matches the computed hash.
    
    This ensures that:
    1. The pinned hash in network_manifest.py is correct
    2. The pinned hash in network_params.py is correct
    3. The genesis file hasn't been modified without updating pinned hashes
    """
    from core.network_manifest import MAINNET_MANIFEST, compute_genesis_hash
    from core.network_params import MAINNET_GENESIS_HASH_HEX, get_pinned_genesis_hash
    
    # Get pinned hash from manifest
    manifest_pinned = MAINNET_MANIFEST.pinned_genesis_hash
    manifest_pinned_hex = MAINNET_MANIFEST.pinned_genesis_hash_hex
    
    # Get pinned hash from network_params
    params_pinned = get_pinned_genesis_hash(chain_id=0)
    
    # Compute actual hash from genesis file
    genesis_path = MAINNET_MANIFEST.genesis_path
    assert genesis_path.exists(), f"Mainnet genesis file not found: {genesis_path}"
    
    computed_hash = compute_genesis_hash(genesis_path)
    computed_hex = "0x" + computed_hash.hex()
    
    # All hashes should match
    assert manifest_pinned == computed_hash, (
        f"Mainnet genesis hash mismatch!\n"
        f"  Pinned (network_manifest): {manifest_pinned_hex}\n"
        f"  Computed (genesis file):   {computed_hex}\n"
        f"  Genesis file: {genesis_path}"
    )
    
    assert params_pinned == computed_hash, (
        f"Mainnet genesis hash mismatch!\n"
        f"  Pinned (network_params):   {MAINNET_GENESIS_HASH_HEX}\n"
        f"  Computed (genesis file):   {computed_hex}\n"
        f"  Genesis file: {genesis_path}"
    )
    
    print(f"✓ Mainnet genesis hash is consistent: {computed_hex}")
    print(f"  Genesis file: {genesis_path}")


def test_network_manifest_available():
    """
    Verify that network_manifest module is importable and contains all networks.
    
    This is critical for mainnet to ensure network identity is available.
    """
    try:
        from core.network_manifest import (
            MAINNET_MANIFEST,
            TESTNET_MANIFEST,
            DEVNET_MANIFEST,
            get_manifest,
        )
    except ImportError as exc:
        raise AssertionError(
            f"CRITICAL: core.network_manifest not available! "
            f"This is a fatal error for mainnet. Import error: {exc}"
        ) from exc
    
    # Verify manifests are defined
    assert MAINNET_MANIFEST is not None
    assert TESTNET_MANIFEST is not None
    assert DEVNET_MANIFEST is not None
    
    # Verify get_manifest works
    mainnet = get_manifest(network="mainnet")
    assert mainnet is not None
    assert mainnet.chain_id == 0
    
    testnet = get_manifest(network="testnet")
    assert testnet is not None
    assert testnet.chain_id == 2
    
    devnet = get_manifest(network="devnet")
    assert devnet is not None
    assert devnet.chain_id == 1337
    
    print("✓ network_manifest is available and all networks are defined")


def test_mainnet_identity_summary():
    """
    Print a summary of mainnet identity for documentation purposes.
    
    This is not a test but a documentation helper that prints the canonical
    mainnet identity values.
    """
    from core.network_manifest import MAINNET_MANIFEST
    
    print("\n" + "=" * 70)
    print("MAINNET CANONICAL IDENTITY")
    print("=" * 70)
    print(f"Network Name:        {MAINNET_MANIFEST.network_name}")
    print(f"Chain ID:            {MAINNET_MANIFEST.chain_id}")
    print(f"Genesis File:        {MAINNET_MANIFEST.genesis_path}")
    print(f"Genesis Hash:        {MAINNET_MANIFEST.pinned_genesis_hash_hex}")
    print(f"HRP (Address Prefix): {MAINNET_MANIFEST.hrp}")
    print(f"Protocol Version:    {MAINNET_MANIFEST.protocol_version}")
    print(f"P2P Network ID:      {MAINNET_MANIFEST.p2p_network_id}")
    print("=" * 70)
    print()
    print("This is the single source of truth for mainnet identity.")
    print("All components must use these values from core.network_manifest.")
    print("=" * 70 + "\n")


def test_docker_genesis_file_exists():
    """
    Verify that genesis files exist at the expected paths.
    
    This ensures they will be included in Docker containers.
    """
    from core.network_manifest import MAINNET_MANIFEST, TESTNET_MANIFEST, DEVNET_MANIFEST
    
    for manifest in [MAINNET_MANIFEST, TESTNET_MANIFEST, DEVNET_MANIFEST]:
        genesis_path = manifest.genesis_path
        assert genesis_path.exists(), (
            f"Genesis file not found for {manifest.network_name}: {genesis_path}\n"
            f"This file must exist and be included in Docker containers."
        )
        
        # Verify it's readable and valid JSON
        try:
            genesis_data = json.loads(genesis_path.read_text())
            assert "chainId" in genesis_data
            assert genesis_data["chainId"] == manifest.chain_id
        except Exception as exc:
            raise AssertionError(
                f"Genesis file is not valid JSON for {manifest.network_name}: {genesis_path}\n"
                f"Error: {exc}"
            ) from exc
        
        print(f"✓ {manifest.network_name} genesis file exists and is valid: {genesis_path}")
