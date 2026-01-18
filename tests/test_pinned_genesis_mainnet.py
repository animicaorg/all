"""
Regression test for pinned genesis hash validation.

This test ensures that the pinned genesis hashes in core/network_params.py
match the actual computed hashes from the genesis JSON files. This prevents
silent genesis mismatches that cause node startup failures.

Critical requirement: If you modify a network genesis file, you MUST:
1. Update the pinned hash constant in core/network_params.py
2. Update this test to reflect the new hash

This test runs in CI to catch accidental genesis file changes.
"""

import pytest
from pathlib import Path

from core.genesis.loader import compute_genesis_hash
from core.network_params import (
    MAINNET_GENESIS_HASH_HEX,
    TESTNET_GENESIS_HASH_HEX,
    DEVNET_GENESIS_HASH_HEX,
    PINNED_GENESIS_BY_NETWORK,
    GENESIS_PATH_BY_NETWORK,
)


def test_mainnet_pinned_genesis_hash_matches_computed():
    """
    Test that the pinned mainnet genesis hash matches the computed hash
    from core/genesis/mainnet.json.
    
    This is a regression test for the issue where mainnet genesis hash
    was pinned to 0xd2d2... but the actual computed hash was 0x6a27...
    causing Docker container startup failures.
    """
    genesis_path = GENESIS_PATH_BY_NETWORK[("mainnet", 0)]
    assert genesis_path.exists(), f"Mainnet genesis file not found: {genesis_path}"
    
    # Compute the canonical hash using the same code path as enforce_pinned_genesis
    computed_hash = compute_genesis_hash(genesis_path, chain_id=0)
    
    # Get the pinned hash
    pinned_hash = MAINNET_GENESIS_HASH_HEX
    
    assert computed_hash == pinned_hash, (
        f"Mainnet genesis hash mismatch!\n"
        f"  Genesis file: {genesis_path}\n"
        f"  Computed hash: {computed_hash}\n"
        f"  Pinned hash:   {pinned_hash}\n"
        f"\n"
        f"If you intentionally changed the mainnet genesis file, you MUST:\n"
        f"1. Update MAINNET_GENESIS_HASH_HEX in core/network_params.py to: {computed_hash}\n"
        f"2. Update this test's assertion to expect the new hash\n"
        f"\n"
        f"This prevents 'genesis does not match pinned network genesis' errors."
    )


def test_testnet_pinned_genesis_hash_matches_computed():
    """
    Test that the pinned testnet genesis hash matches the computed hash
    from core/genesis/testnet.json.
    """
    genesis_path = GENESIS_PATH_BY_NETWORK[("testnet", 2)]
    
    # Skip if testnet genesis doesn't exist (optional network)
    if not genesis_path.exists():
        pytest.skip(f"Testnet genesis file not found: {genesis_path}")
    
    # Compute the canonical hash
    computed_hash = compute_genesis_hash(genesis_path, chain_id=2)
    
    # Get the pinned hash
    pinned_hash = TESTNET_GENESIS_HASH_HEX
    
    assert computed_hash == pinned_hash, (
        f"Testnet genesis hash mismatch!\n"
        f"  Genesis file: {genesis_path}\n"
        f"  Computed hash: {computed_hash}\n"
        f"  Pinned hash:   {pinned_hash}\n"
        f"\n"
        f"Update TESTNET_GENESIS_HASH_HEX in core/network_params.py to: {computed_hash}"
    )


def test_devnet_pinned_genesis_hash_matches_computed():
    """
    Test that the pinned devnet genesis hash matches the computed hash
    from core/genesis/devnet.json.
    """
    genesis_path = GENESIS_PATH_BY_NETWORK[("devnet", 1337)]
    
    # Skip if devnet genesis doesn't exist (optional network)
    if not genesis_path.exists():
        pytest.skip(f"Devnet genesis file not found: {genesis_path}")
    
    # Compute the canonical hash
    computed_hash = compute_genesis_hash(genesis_path, chain_id=1337)
    
    # Get the pinned hash
    pinned_hash = DEVNET_GENESIS_HASH_HEX
    
    assert computed_hash == pinned_hash, (
        f"Devnet genesis hash mismatch!\n"
        f"  Genesis file: {genesis_path}\n"
        f"  Computed hash: {computed_hash}\n"
        f"  Pinned hash:   {pinned_hash}\n"
        f"\n"
        f"Update DEVNET_GENESIS_HASH_HEX in core/network_params.py to: {computed_hash}"
    )


def test_pinned_genesis_by_network_dict_consistency():
    """
    Test that PINNED_GENESIS_BY_NETWORK dictionary matches individual constants.
    """
    # Verify mainnet
    assert PINNED_GENESIS_BY_NETWORK[("mainnet", 0)].hex() == MAINNET_GENESIS_HASH_HEX[2:], (
        "PINNED_GENESIS_BY_NETWORK[('mainnet', 0)] doesn't match MAINNET_GENESIS_HASH_HEX"
    )
    
    # Verify testnet
    assert PINNED_GENESIS_BY_NETWORK[("testnet", 2)].hex() == TESTNET_GENESIS_HASH_HEX[2:], (
        "PINNED_GENESIS_BY_NETWORK[('testnet', 2)] doesn't match TESTNET_GENESIS_HASH_HEX"
    )
    
    # Verify devnet
    assert PINNED_GENESIS_BY_NETWORK[("devnet", 1337)].hex() == DEVNET_GENESIS_HASH_HEX[2:], (
        "PINNED_GENESIS_BY_NETWORK[('devnet', 1337)] doesn't match DEVNET_GENESIS_HASH_HEX"
    )


def test_mainnet_chain_id_is_zero():
    """
    Verify that mainnet uses chain_id=0 everywhere.
    This is critical for P2P identity and sync.
    """
    import json
    
    genesis_path = GENESIS_PATH_BY_NETWORK[("mainnet", 0)]
    with open(genesis_path) as f:
        genesis = json.load(f)
    
    assert genesis["chainId"] == 0, (
        f"Mainnet genesis MUST have chainId=0, found {genesis['chainId']}"
    )
    
    # Verify it's in the tuple key
    assert ("mainnet", 0) in PINNED_GENESIS_BY_NETWORK, (
        "PINNED_GENESIS_BY_NETWORK must have ('mainnet', 0) key"
    )
    
    assert ("mainnet", 0) in GENESIS_PATH_BY_NETWORK, (
        "GENESIS_PATH_BY_NETWORK must have ('mainnet', 0) key"
    )


if __name__ == "__main__":
    # Allow running this test file directly
    pytest.main([__file__, "-v"])
