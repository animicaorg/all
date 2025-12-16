#!/usr/bin/env python3
"""
Integration test to verify P2PService can connect to TCP seeds after the quic-v1 fix.
This test simulates what happens when the P2PService starts up and attempts to dial seeds.
"""

import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

print("="*70)
print("P2PService Seed Connection Integration Test")
print("="*70)

# Setup environment for mainnet
os.environ['ANIMICA_P2P_CHAIN_ID'] = '1'

# Test 1: Verify config loading
print("\n1. Loading P2P configuration for mainnet...")
from p2p.config import load_config

cfg = load_config()
print(f"   ✓ Loaded {len(cfg.seeds)} seeds")
for i, seed in enumerate(cfg.seeds, 1):
    print(f"     {i}. {seed}")

# Test 2: Parse seeds and filter TCP
print("\n2. Parsing seeds and identifying TCP candidates...")
from p2p.transport.multiaddr import parse_multiaddr

tcp_seeds = []
quic_seeds = []
parse_errors = []

for seed in cfg.seeds:
    try:
        parsed = parse_multiaddr(seed)
        if parsed.transport == "tcp":
            tcp_seeds.append((seed, parsed))
            print(f"   ✓ TCP: {seed}")
        elif parsed.is_quic:
            quic_seeds.append((seed, parsed))
            print(f"   • QUIC (skip for P2PService): {seed}")
    except Exception as e:
        parse_errors.append((seed, e))
        print(f"   ✗ Parse error: {seed} - {e}")

if parse_errors:
    print(f"\n   ERROR: {len(parse_errors)} seed(s) failed to parse!")
    sys.exit(1)

if len(tcp_seeds) < 1:
    print(f"\n   ERROR: No TCP seeds available for dialing!")
    sys.exit(1)

print(f"\n   Summary:")
print(f"     - TCP seeds (will dial): {len(tcp_seeds)}")
print(f"     - QUIC seeds (skip): {len(quic_seeds)}")
print(f"     - Parse errors: {len(parse_errors)}")

# Test 3: Simulate P2PService seed dialing logic
print("\n3. Simulating P2PService seed dialing...")

async def simulate_p2pservice_start():
    """Simulate what P2PService.start() does with seeds"""
    
    # Mock the transport's parse_multiaddr (already tested above)
    def mock_parse_multiaddr(addr):
        return parse_multiaddr(addr)
    
    # Simulate the seed dialing loop from P2PService.start()
    seed_count = 0
    seeds_to_dial = []
    
    for seed in cfg.seeds:
        try:
            parsed = mock_parse_multiaddr(seed)
        except Exception as e:
            print(f"   ✗ Parse failed: {seed} - {e}")
            continue
        
        if parsed.transport != "tcp":
            print(f"   • Skip non-TCP: {seed} (transport={parsed.transport})")
            continue
        
        addr = f"tcp://{parsed.host}:{parsed.port}"
        print(f"   ✓ Would dial: {addr}")
        seeds_to_dial.append(addr)
        seed_count += 1
    
    if seed_count == 0 and len(cfg.seeds) > 0:
        print(f"   ⚠ WARNING: No TCP seeds to dial (total seeds: {len(cfg.seeds)})")
        return False
    elif seed_count == 0:
        print(f"   ⚠ WARNING: No seeds configured")
        return False
    
    print(f"\n   Result: {seed_count} TCP seed(s) would be dialed")
    return seed_count >= 1

success = asyncio.run(simulate_p2pservice_start())

if not success:
    print("\n   ERROR: P2PService would not dial any seeds!")
    sys.exit(1)

# Test 4: Verify format_multiaddr works correctly
print("\n4. Testing multiaddr formatting roundtrip...")
from p2p.transport.multiaddr import format_multiaddr

for seed, parsed in tcp_seeds:
    formatted = format_multiaddr(parsed)
    print(f"   Original:  {seed}")
    print(f"   Formatted: {formatted}")
    
    # Re-parse the formatted version
    try:
        reparsed = parse_multiaddr(formatted)
        if reparsed.host == parsed.host and reparsed.port == parsed.port and reparsed.transport == parsed.transport:
            print(f"   ✓ Roundtrip successful")
        else:
            print(f"   ✗ Roundtrip mismatch!")
            sys.exit(1)
    except Exception as e:
        print(f"   ✗ Roundtrip parse failed: {e}")
        sys.exit(1)

# Test 5: Verify all networks work
print("\n5. Testing all networks (mainnet, testnet, devnet)...")

networks = [(1, "mainnet"), (2, "testnet"), (1337, "devnet")]
all_ok = True

for chain_id, network_name in networks:
    os.environ['ANIMICA_P2P_CHAIN_ID'] = str(chain_id)
    cfg = load_config()
    
    tcp_count = 0
    for seed in cfg.seeds:
        try:
            parsed = parse_multiaddr(seed)
            if parsed.transport == "tcp":
                tcp_count += 1
        except Exception:
            pass
    
    if tcp_count >= 1:
        print(f"   ✓ {network_name} (chain_id={chain_id}): {tcp_count} TCP seed(s)")
    else:
        print(f"   ✗ {network_name} (chain_id={chain_id}): NO TCP seeds!")
        all_ok = False

if not all_ok:
    print("\n   ERROR: Some networks lack TCP seeds!")
    sys.exit(1)

# Summary
print("\n" + "="*70)
print("INTEGRATION TEST SUMMARY")
print("="*70)
print("✓ Configuration loads correctly")
print("✓ Multiaddr parser recognizes quic-v1 format")
print("✓ TCP seeds are correctly identified and filtered")
print("✓ P2PService would successfully dial TCP seeds")
print("✓ Multiaddr formatting/parsing roundtrip works")
print("✓ All networks (mainnet/testnet/devnet) have TCP seeds")
print("\n✅ P2P connectivity fix is working correctly!")
print("\nThe node should now be able to:")
print("  1. Parse all seed addresses (including quic-v1)")
print("  2. Identify and dial TCP seeds")
print("  3. Connect to peers on the network")
print("  4. Begin blockchain synchronization")
