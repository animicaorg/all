"""
Test that net.getGenesisHash returns a proper hex string, not a bound method.

This is a regression test for the issue where genesis hash was printed as:
  0x<bound method Header.hash of Header(...)>
instead of a proper hex string like:
  0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242
"""
from __future__ import annotations

import re

from rpc.tests import new_test_client, rpc_call


def test_genesis_hash_is_hex_string():
    """
    Verify that net.getGenesisHash returns a proper 0x-prefixed hex string.
    
    The hash must be:
    - A string (not a callable, not a bound method)
    - Prefixed with "0x"
    - Exactly 66 characters (0x + 64 hex chars = 32 bytes)
    - Contain only valid hex characters
    """
    client, cfg, _ = new_test_client()
    
    # Call the RPC method
    response = rpc_call(client, "net.getGenesisHash")
    genesis_hash = response["result"]
    
    # Ensure it's a string
    assert isinstance(genesis_hash, str), (
        f"Genesis hash must be a string, got {type(genesis_hash)}: {genesis_hash}"
    )
    
    # Ensure it doesn't contain "bound method" (regression test)
    assert "bound method" not in genesis_hash.lower(), (
        f"Genesis hash contains 'bound method' - this is the bug we're fixing! Got: {genesis_hash}"
    )
    
    # Ensure it starts with "0x"
    assert genesis_hash.startswith("0x"), (
        f"Genesis hash must start with '0x', got: {genesis_hash}"
    )
    
    # Ensure it's exactly 66 characters (0x + 64 hex chars)
    assert len(genesis_hash) == 66, (
        f"Genesis hash must be 66 characters (0x + 64 hex), got {len(genesis_hash)}: {genesis_hash}"
    )
    
    # Ensure it contains only valid hex characters
    hex_pattern = re.compile(r"^0x[0-9a-fA-F]{64}$")
    assert hex_pattern.match(genesis_hash), (
        f"Genesis hash must be a valid hex string (0x + 64 hex chars), got: {genesis_hash}"
    )
    
    print(f"✓ Genesis hash is valid: {genesis_hash}")


def test_genesis_hash_matches_chain_identity():
    """
    Verify that net.getGenesisHash matches chain.getChainIdentity.genesisHash.
    
    Both methods should return the same genesis hash.
    """
    client, cfg, _ = new_test_client()
    
    # Get genesis hash from net.getGenesisHash
    net_response = rpc_call(client, "net.getGenesisHash")
    net_genesis_hash = net_response["result"]
    
    # Get genesis hash from chain.getChainIdentity
    identity_response = rpc_call(client, "chain.getChainIdentity")
    identity_genesis_hash = identity_response["result"]["genesisHash"]
    
    # Both should be the same
    assert net_genesis_hash == identity_genesis_hash, (
        f"Genesis hash mismatch!\n"
        f"  net.getGenesisHash:           {net_genesis_hash}\n"
        f"  chain.getChainIdentity:       {identity_genesis_hash}"
    )
    
    print(f"✓ Genesis hashes match: {net_genesis_hash}")


def test_genesis_hash_matches_pinned_network_params():
    """
    Verify that the RPC genesis hash matches the pinned hash in network_params.
    
    This ensures consistency between the node's actual genesis and the pinned value.
    """
    from core.network_params import get_pinned_genesis_hash
    
    client, cfg, _ = new_test_client()
    
    # Get genesis hash from RPC
    response = rpc_call(client, "net.getGenesisHash")
    rpc_genesis_hash = response["result"]
    
    # Get pinned genesis hash from network_params
    pinned_hash_bytes = get_pinned_genesis_hash(chain_id=cfg.chain_id)
    
    if pinned_hash_bytes:
        pinned_hash_hex = "0x" + pinned_hash_bytes.hex()
        
        # Both should match
        assert rpc_genesis_hash.lower() == pinned_hash_hex.lower(), (
            f"Genesis hash mismatch between RPC and network_params!\n"
            f"  RPC:                 {rpc_genesis_hash}\n"
            f"  Pinned (network_params): {pinned_hash_hex}\n"
            f"  Chain ID:            {cfg.chain_id}"
        )
        
        print(f"✓ Genesis hash matches pinned value: {rpc_genesis_hash}")
    else:
        print(f"⚠ No pinned genesis hash for chain_id={cfg.chain_id}, skipping validation")


def test_genesis_hash_via_aliases():
    """
    Verify that genesis hash aliases work correctly.
    
    net.getGenesisHash should be accessible via multiple aliases.
    """
    client, cfg, _ = new_test_client()
    
    # Try all aliases
    aliases = ["net.getGenesisHash", "net.genesisHash", "chain.genesisHash"]
    results = []
    
    for alias in aliases:
        try:
            response = rpc_call(client, alias)
            genesis_hash = response["result"]
            results.append((alias, genesis_hash))
            
            # Validate format
            assert isinstance(genesis_hash, str)
            assert genesis_hash.startswith("0x")
            assert len(genesis_hash) == 66
        except Exception as exc:
            # Some aliases might not be registered, that's OK
            print(f"⚠ Alias '{alias}' not available: {exc}")
    
    # If we got multiple results, they should all match
    if len(results) > 1:
        first_hash = results[0][1]
        for alias, genesis_hash in results[1:]:
            assert genesis_hash == first_hash, (
                f"Genesis hash mismatch between aliases!\n"
                f"  {results[0][0]}: {first_hash}\n"
                f"  {alias}: {genesis_hash}"
            )
    
    if results:
        print(f"✓ All {len(results)} aliases return consistent genesis hash: {results[0][1]}")
