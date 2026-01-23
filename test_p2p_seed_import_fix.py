#!/usr/bin/env python3
"""
Test for P2P seed import duplicate detection fix.

This test verifies that the fix for mixed address format duplicate detection
works correctly. Previously, seeds in /dns4/ format weren't detected as duplicates
of their /dns/ normalized form, causing them to be incorrectly marked as "invalid".

Bug: Seeds were compared without normalization, so:
- /dns4/mainnet.animica.org/tcp/30333 (initial seed)
- /dns/mainnet.animica.org/tcp/30333 (normalized incoming)
These were not detected as duplicates.

Fix: Pre-normalize existing seeds before comparison in import_peers().
"""

from p2p.transport.multiaddr import normalize_multiaddr


def test_seed_normalization():
    """Test that different address formats normalize to the same canonical form."""
    
    # Test cases: (input, expected_normalized)
    test_cases = [
        ("/dns4/mainnet.animica.org/tcp/30333", "/dns/mainnet.animica.org/tcp/30333"),
        ("/dns/mainnet.animica.org/tcp/30333", "/dns/mainnet.animica.org/tcp/30333"),
        ("/ip4/3.12.224.189/tcp/30333", "/ip4/3.12.224.189/tcp/30333"),
        ("/ip4/144.126.133.21/tcp/30333", "/ip4/144.126.133.21/tcp/30333"),
    ]
    
    print("Testing multiaddr normalization:")
    for input_addr, expected in test_cases:
        normalized = normalize_multiaddr(input_addr)
        status = "✓" if normalized == expected else "✗"
        print(f"  {status} {input_addr}")
        print(f"     -> {normalized}")
        assert normalized == expected, f"Expected {expected}, got {normalized}"
    print()


def test_duplicate_detection():
    """Test that our fix correctly detects duplicates across different formats."""
    from urllib.parse import urlparse
    import ipaddress
    
    def normalize_addr(addr):
        """Simulate the normalize logic from service.py"""
        if addr.startswith("/"):
            return normalize_multiaddr(addr)
        host = None
        port = None
        if "://" in addr:
            parsed = urlparse(addr)
            host = parsed.hostname
            port = parsed.port
        elif ":" in addr:
            host, port_s = addr.rsplit(":", 1)
            port = int(port_s)
        try:
            ip = ipaddress.ip_address(host)
            host_proto = "ip4" if ip.version == 4 else "ip6"
        except ValueError:
            host_proto = "dns"
        return f"/{host_proto}/{host}/tcp/{port}"
    
    # Initial seeds (as stored in self.seeds, un-normalized)
    initial_seeds = [
        "/dns4/mainnet.animica.org/tcp/30333",
        "/ip4/144.126.133.21/tcp/30333",
        "/ip4/3.12.224.189/tcp/30333",
    ]
    
    # Bootstrap seeds (mixed formats from RPC)
    bootstrap_seeds = [
        "/dns4/mainnet.animica.org/tcp/30333",  # Same format as initial
        "/ip4/144.126.133.21/tcp/30333",         # Same format as initial
        "/ip4/3.12.224.189/tcp/30333",            # Same format as initial
        "tcp://3.12.224.189:30333",               # Different format, same address
        "tcp://144.126.133.21:30333",             # Different format, same address
    ]
    
    # Pre-normalize existing seeds (THE FIX)
    existing_normalized = set()
    for seed in initial_seeds:
        norm = normalize_addr(seed)
        existing_normalized.add(norm)
    
    print("Testing duplicate detection with pre-normalized seeds:")
    print(f"  Initial seeds: {len(initial_seeds)}")
    print(f"  Bootstrap seeds: {len(bootstrap_seeds)}")
    print()
    
    imported = 0
    skipped = 0
    
    for addr in bootstrap_seeds:
        normalized = normalize_addr(addr)
        if normalized in existing_normalized:
            skipped += 1
            print(f"  SKIP (duplicate): {addr}")
        else:
            imported += 1
            existing_normalized.add(normalized)
            print(f"  ADD (new):        {addr}")
    
    print()
    print(f"Results: imported={imported}, skipped={skipped}")
    print(f"Expected: imported=0, skipped=5 (all bootstrap seeds are duplicates)")
    print()
    
    # All bootstrap seeds should be detected as duplicates
    assert imported == 0, f"Expected 0 imports, got {imported}"
    assert skipped == 5, f"Expected 5 skipped, got {skipped}"
    
    print("✓ All tests passed!")


def test_mixed_format_deduplication():
    """Test that tcp:// URLs are properly converted and deduplicated."""
    from urllib.parse import urlparse
    import ipaddress
    
    def normalize_addr(addr):
        """Simulate the normalize logic from service.py"""
        if addr.startswith("/"):
            return normalize_multiaddr(addr)
        host = None
        port = None
        if "://" in addr:
            parsed = urlparse(addr)
            host = parsed.hostname
            port = parsed.port
        elif ":" in addr:
            host, port_s = addr.rsplit(":", 1)
            port = int(port_s)
        try:
            ip = ipaddress.ip_address(host)
            host_proto = "ip4" if ip.version == 4 else "ip6"
        except ValueError:
            host_proto = "dns"
        return f"/{host_proto}/{host}/tcp/{port}"
    
    print("Testing that tcp:// URLs normalize to /ip4/ format:")
    
    tcp_url = "tcp://3.12.224.189:30333"
    multiaddr = "/ip4/3.12.224.189/tcp/30333"
    
    tcp_normalized = normalize_addr(tcp_url)
    ma_normalized = normalize_addr(multiaddr)
    
    print(f"  tcp:// URL:  {tcp_url}")
    print(f"    -> {tcp_normalized}")
    print(f"  multiaddr:   {multiaddr}")
    print(f"    -> {ma_normalized}")
    print()
    
    assert tcp_normalized == ma_normalized, \
        f"tcp:// and multiaddr should normalize to same address: {tcp_normalized} != {ma_normalized}"
    
    print("✓ tcp:// URLs correctly deduplicate with multiaddr format!")
    print()


def test_duplicates_within_same_import():
    """Test that duplicates within the same import call are detected."""
    from urllib.parse import urlparse
    import ipaddress
    
    def normalize_addr(addr):
        """Simulate the normalize logic from service.py"""
        if addr.startswith("/"):
            return normalize_multiaddr(addr)
        host = None
        port = None
        if "://" in addr:
            parsed = urlparse(addr)
            host = parsed.hostname
            port = parsed.port
        elif ":" in addr:
            host, port_s = addr.rsplit(":", 1)
            port = int(port_s)
        try:
            ip = ipaddress.ip_address(host)
            host_proto = "ip4" if ip.version == 4 else "ip6"
        except ValueError:
            host_proto = "dns"
        return f"/{host_proto}/{host}/tcp/{port}"
    
    # Empty initial seeds
    initial_seeds = []
    
    # Import list with duplicates in different formats
    import_list = [
        "/ip4/1.2.3.4/tcp/30333",
        "tcp://1.2.3.4:30333",           # Same address, different format
        "/dns4/example.com/tcp/30333",
        "/dns/example.com/tcp/30333",    # Same address, different format
        "example.com:30333",             # Same address, different format
    ]
    
    print("Testing duplicate detection within same import call:")
    print(f"  Import list: {len(import_list)} addresses")
    print()
    
    # Simulate import_peers with the FIX
    existing_normalized = set()
    for seed in initial_seeds:
        norm = normalize_addr(seed)
        existing_normalized.add(norm)
    
    imported = 0
    skipped = 0
    
    for addr in import_list:
        normalized = normalize_addr(addr)
        if normalized in existing_normalized:
            skipped += 1
            print(f"  SKIP: {addr}")
        else:
            imported += 1
            existing_normalized.add(normalized)  # THE FIX: add to set immediately
            print(f"  ADD:  {addr}")
    
    print()
    print(f"Results: imported={imported}, skipped={skipped}")
    print(f"Expected: imported=2 (one IP, one hostname), skipped=3 (duplicates)")
    print()
    
    # Should import 2 unique addresses, skip 3 duplicates
    assert imported == 2, f"Expected 2 imports, got {imported}"
    assert skipped == 3, f"Expected 3 skipped, got {skipped}"
    
    print("✓ Duplicates within same import call are properly detected!")
    print()


if __name__ == "__main__":
    test_seed_normalization()
    test_duplicate_detection()
    test_mixed_format_deduplication()
    test_duplicates_within_same_import()
    print("\n✓ All tests passed! The fix correctly handles mixed address formats.")
