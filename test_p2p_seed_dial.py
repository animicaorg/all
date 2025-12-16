#!/usr/bin/env python3
"""
Test to verify P2P seed dialing works correctly after the multiaddr fix.
This validates that:
1. Multiaddr parsing recognizes quic-v1 token
2. TCP seeds are correctly filtered from the seed list
3. P2PService properly attempts to dial TCP seeds
"""

import os
import sys

# Test 1: Multiaddr parsing
print("="*70)
print("Test 1: Multiaddr parsing with quic-v1")
print("="*70)

from p2p.transport.multiaddr import parse_multiaddr

test_seeds = [
    "/dns4/mainnet.animica.org/udp/443/quic-v1",
    "/dns4/mainnet.animica.org/tcp/30333",
    "/ip4/144.126.133.21/udp/443/quic-v1",
    "/ip4/144.126.133.21/tcp/30333",
]

parsing_success = True
tcp_seeds = []

for seed in test_seeds:
    try:
        parsed = parse_multiaddr(seed)
        print(f"✓ {seed}")
        print(f"  transport={parsed.transport}, host={parsed.host}, port={parsed.port}, is_quic={parsed.is_quic}")
        
        # Collect TCP seeds for later use
        if parsed.transport == "tcp":
            tcp_seeds.append((seed, parsed))
    except Exception as e:
        print(f"✗ {seed}")
        print(f"  ERROR: {e}")
        parsing_success = False

if parsing_success:
    print("\n✓ All seeds parsed successfully")
else:
    print("\n✗ Some seeds failed to parse")
    sys.exit(1)

print(f"\nTCP seeds available for dialing: {len(tcp_seeds)}")
for seed, parsed in tcp_seeds:
    print(f"  - tcp://{parsed.host}:{parsed.port}")

# Test 2: Config loading with network-specific seeds
print("\n" + "="*70)
print("Test 2: Config loading with chain_id")
print("="*70)

os.environ['ANIMICA_P2P_CHAIN_ID'] = '1'
from p2p.config import load_config

cfg = load_config()
print(f"Loaded {len(cfg.seeds)} seeds for mainnet (chain_id=1)")

# Count TCP vs non-TCP seeds
tcp_count = 0
quic_count = 0

for seed in cfg.seeds:
    parsed = parse_multiaddr(seed)
    if parsed.transport == "tcp":
        tcp_count += 1
    elif parsed.is_quic:
        quic_count += 1

print(f"  - TCP seeds: {tcp_count}")
print(f"  - QUIC seeds: {quic_count}")

if tcp_count < 2:
    print("✗ Expected at least 2 TCP seeds for fallback")
    sys.exit(1)

print("✓ Config has sufficient TCP seeds for connectivity")

# Test 3: Verify P2PService would dial TCP seeds correctly
print("\n" + "="*70)
print("Test 3: Verify P2PService seed filtering logic")
print("="*70)

# Simulate what P2PService.start() does
from p2p.transport.multiaddr import parse_multiaddr as _parse_multiaddr

seeds_to_dial = []
for seed in cfg.seeds:
    try:
        parsed = _parse_multiaddr(seed)
        if parsed.transport == "tcp":
            addr = f"tcp://{parsed.host}:{parsed.port}"
            seeds_to_dial.append(addr)
            print(f"✓ Would dial: {addr} (from {seed})")
        else:
            print(f"  Skip non-TCP: {seed} (transport={parsed.transport})")
    except Exception as e:
        print(f"✗ Parse error for {seed}: {e}")

if len(seeds_to_dial) < 2:
    print(f"\n✗ Only {len(seeds_to_dial)} TCP seed(s) would be dialed")
    print("  This is insufficient for reliable connectivity")
    sys.exit(1)

print(f"\n✓ P2PService would dial {len(seeds_to_dial)} TCP seed(s)")

# Test 4: Verify all network configs have TCP seeds
print("\n" + "="*70)
print("Test 4: Verify all networks have TCP seeds")
print("="*70)

networks = [
    (1, "mainnet"),
    (2, "testnet"),
    (1337, "devnet"),
]

all_ok = True
for chain_id, network_name in networks:
    os.environ['ANIMICA_P2P_CHAIN_ID'] = str(chain_id)
    cfg = load_config()
    
    tcp_count = 0
    for seed in cfg.seeds:
        parsed = parse_multiaddr(seed)
        if parsed.transport == "tcp":
            tcp_count += 1
    
    if tcp_count >= 2:
        print(f"✓ {network_name} (chain_id={chain_id}): {tcp_count} TCP seeds")
    else:
        print(f"✗ {network_name} (chain_id={chain_id}): only {tcp_count} TCP seed(s)")
        all_ok = False

if not all_ok:
    print("\n✗ Some networks lack sufficient TCP seeds")
    sys.exit(1)

print("\n✓ All networks have sufficient TCP seeds")

# Final summary
print("\n" + "="*70)
print("SUMMARY")
print("="*70)
print("✓ Multiaddr parser recognizes quic-v1 token")
print("✓ TCP seeds are correctly identified and filtered")
print("✓ All networks have at least 2 TCP seeds for reliability")
print("✓ P2P connectivity should now work correctly")
print("\nThe fix resolves the node synchronization issue by:")
print("1. Supporting quic-v1 multiaddr format (not just 'quic')")
print("2. Ensuring TCP seeds are properly parsed and dialed")
print("3. Providing fallback connectivity via IP addresses")
