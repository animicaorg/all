"""
Test to verify multiaddr parser correctly handles quic-v1 token.
This is the fix for the P2P connectivity issue where nodes couldn't sync.
"""

import pytest
from p2p.transport.multiaddr import parse_multiaddr, MultiaddrParseError


class TestQuicV1MultiaddrParsing:
    """Test that multiaddr parser recognizes quic-v1 protocol token."""
    
    def test_parse_quic_v1_dns_seed(self):
        """Test parsing DNS-based QUIC seed with quic-v1 token."""
        result = parse_multiaddr("/dns4/bootstrap.example.net/udp/443/quic-v1")
        
        assert result.transport == "udp"
        assert result.host == "bootstrap.example.net"
        assert result.port == 443
        assert result.is_quic is True
    
    def test_parse_quic_v1_ip_seed(self):
        """Test parsing IP-based QUIC seed with quic-v1 token."""
        result = parse_multiaddr("/ip4/144.126.133.21/udp/443/quic-v1")
        
        assert result.transport == "udp"
        assert result.host == "144.126.133.21"
        assert result.port == 443
        assert result.is_quic is True
    
    def test_parse_tcp_dns_seed(self):
        """Test parsing DNS-based TCP seed (for comparison)."""
        result = parse_multiaddr("/dns4/bootstrap.example.net/tcp/30333")
        
        assert result.transport == "tcp"
        assert result.host == "bootstrap.example.net"
        assert result.port == 30333
        assert result.is_quic is False
    
    def test_parse_tcp_ip_seed(self):
        """Test parsing IP-based TCP seed (for comparison)."""
        result = parse_multiaddr("/ip4/144.126.133.21/tcp/30333")
        
        assert result.transport == "tcp"
        assert result.host == "144.126.133.21"
        assert result.port == 30333
        assert result.is_quic is False
    
    def test_legacy_quic_token_still_works(self):
        """Test that legacy 'quic' token (without version) still works."""
        result = parse_multiaddr("/ip4/1.2.3.4/udp/443/quic")
        
        assert result.transport == "udp"
        assert result.host == "1.2.3.4"
        assert result.port == 443
        assert result.is_quic is True
    
    def test_all_default_mainnet_seeds_parse(self):
        """Test that all default mainnet seeds parse correctly."""
        from p2p import config as p2p_config

        seeds = list(p2p_config.DEFAULT_SEEDS_BY_NETWORK[1])
        
        # All should parse without errors
        results = [parse_multiaddr(seed) for seed in seeds]
        
        # Should have at least one TCP seed
        tcp_count = sum(1 for r in results if r.transport == "tcp" and not r.is_quic)

        assert tcp_count >= 1, f"Expected TCP seeds, got {tcp_count}"


class TestP2PServiceSeedFiltering:
    """Test that P2PService correctly filters TCP seeds for dialing."""
    
    def test_tcp_seeds_identified_correctly(self):
        """Test that TCP seeds are identified and QUIC seeds are filtered."""
        from p2p.config import load_config
        import os
        
        # Use mainnet seeds
        os.environ['ANIMICA_P2P_CHAIN_ID'] = '1'
        cfg = load_config()
        
        tcp_seeds = []
        for seed in cfg.seeds:
            parsed = parse_multiaddr(seed)
            if parsed.transport == "tcp":
                tcp_seeds.append(seed)
        
        # Should have TCP seeds for mainnet
        assert any("144.126.133.21" in seed for seed in tcp_seeds)
        
        # Verify they can be dialed
        for seed in tcp_seeds:
            parsed = parse_multiaddr(seed)
            assert parsed.port is not None, f"TCP seed missing port: {seed}"
            assert parsed.host, f"TCP seed missing host: {seed}"


class TestMultiaddrRoundtrip:
    """Test that multiaddr formatting/parsing roundtrip works."""
    
    def test_tcp_seed_roundtrip(self):
        """Test parsing and formatting TCP seed."""
        from p2p.transport.multiaddr import format_multiaddr
        
        original = "/ip4/144.126.133.21/tcp/30333"
        parsed = parse_multiaddr(original)
        formatted = format_multiaddr(parsed)
        reparsed = parse_multiaddr(formatted)
        
        assert parsed.host == reparsed.host
        assert parsed.port == reparsed.port
        assert parsed.transport == reparsed.transport
    
    def test_quic_v1_seed_roundtrip(self):
        """Test parsing and formatting QUIC-v1 seed."""
        from p2p.transport.multiaddr import format_multiaddr
        
        original = "/ip4/144.126.133.21/udp/443/quic-v1"
        parsed = parse_multiaddr(original)
        formatted = format_multiaddr(parsed)
        reparsed = parse_multiaddr(formatted)
        
        assert parsed.host == reparsed.host
        assert parsed.port == reparsed.port
        assert parsed.transport == reparsed.transport
        assert parsed.is_quic == reparsed.is_quic
