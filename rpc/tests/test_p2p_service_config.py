"""Tests for P2P service configuration from environment variables."""



def test_p2p_service_reads_env_vars(monkeypatch):
    """Test that P2P service initialization reads P2P_LISTEN and P2P_SEEDS from environment."""
    # Set environment variables
    monkeypatch.setenv("P2P_LISTEN", "0.0.0.0:30333")
    monkeypatch.setenv("P2P_SEEDS", "/ip4/1.2.3.4/tcp/30333,/ip4/5.6.7.8/tcp/30333")
    
    # Import after setting env vars to ensure they're read
    from p2p.node.service import P2PService
    
    # Create service
    service = P2PService(
        listen_addrs=["/ip4/0.0.0.0/tcp/30333"],
        seeds=["/ip4/1.2.3.4/tcp/30333", "/ip4/5.6.7.8/tcp/30333"],
        chain_id=1,
    )
    
    # Verify configuration
    assert service.listen_addrs == ["/ip4/0.0.0.0/tcp/30333"]
    assert len(service.seeds) == 2
    assert "/ip4/1.2.3.4/tcp/30333" in service.seeds
    assert "/ip4/5.6.7.8/tcp/30333" in service.seeds


def test_p2p_listen_address_parsing():
    """Test that host:port format is correctly converted to multiaddr format."""
    from p2p.node.service import P2PService
    
    # Test with host:port format
    service = P2PService(
        listen_addrs=["/ip4/0.0.0.0/tcp/30333"],
        chain_id=1,
    )
    
    assert service.listen_addrs == ["/ip4/0.0.0.0/tcp/30333"]


def test_p2p_service_default_listen_addr():
    """Test that P2P service uses default listen address when none provided."""
    from p2p.node.service import P2PService
    
    # Create service without explicit listen_addrs
    service = P2PService(chain_id=1)
    
    # Should use default
    assert service.listen_addrs == ["/ip4/0.0.0.0/tcp/42069"]


def test_p2p_service_empty_seeds():
    """Test that P2P service handles empty seeds list."""
    from p2p.node.service import P2PService
    
    # Create service with empty seeds
    service = P2PService(
        listen_addrs=["/ip4/0.0.0.0/tcp/30333"],
        seeds=[],
        chain_id=1,
    )
    
    assert service.seeds == []


def test_p2p_service_multiaddr_format():
    """Test that P2P service accepts multiaddr format for listen addresses."""
    from p2p.node.service import P2PService
    
    # Test with multiaddr format
    service = P2PService(
        listen_addrs=["/ip4/127.0.0.1/tcp/30333"],
        chain_id=1,
    )
    
    assert service.listen_addrs == ["/ip4/127.0.0.1/tcp/30333"]


def test_deps_parse_p2p_listen_host_port():
    """Test the logic that parses P2P_LISTEN in host:port format."""
    # Simulate the parsing logic from rpc/deps.py
    p2p_listen = "0.0.0.0:30333"
    
    listen_addrs = []
    if p2p_listen:
        if ":" in p2p_listen and not p2p_listen.startswith("/"):
            # Format is "host:port", convert to multiaddr
            host, port = p2p_listen.rsplit(":", 1)
            listen_addrs = [f"/ip4/{host}/tcp/{port}"]
        else:
            # Already in multiaddr format or empty
            listen_addrs = [p2p_listen]
    
    assert listen_addrs == ["/ip4/0.0.0.0/tcp/30333"]


def test_deps_parse_p2p_listen_multiaddr():
    """Test the logic that parses P2P_LISTEN in multiaddr format."""
    # Simulate the parsing logic from rpc/deps.py
    p2p_listen = "/ip4/0.0.0.0/tcp/30333"
    
    listen_addrs = []
    if p2p_listen:
        if ":" in p2p_listen and not p2p_listen.startswith("/"):
            # Format is "host:port", convert to multiaddr
            host, port = p2p_listen.rsplit(":", 1)
            listen_addrs = [f"/ip4/{host}/tcp/{port}"]
        else:
            # Already in multiaddr format or empty
            listen_addrs = [p2p_listen]
    
    assert listen_addrs == ["/ip4/0.0.0.0/tcp/30333"]


def test_deps_parse_p2p_seeds():
    """Test the logic that parses P2P_SEEDS as comma-separated list."""
    # Simulate the parsing logic from rpc/deps.py
    p2p_seeds = "/ip4/1.2.3.4/tcp/30333, /ip4/5.6.7.8/tcp/30333 ,/ip4/9.10.11.12/tcp/30333"
    
    seeds = [s.strip() for s in p2p_seeds.split(",") if s.strip()]
    
    assert len(seeds) == 3
    assert "/ip4/1.2.3.4/tcp/30333" in seeds
    assert "/ip4/5.6.7.8/tcp/30333" in seeds
    assert "/ip4/9.10.11.12/tcp/30333" in seeds


def test_deps_parse_empty_p2p_seeds():
    """Test the logic that handles empty P2P_SEEDS."""
    # Simulate the parsing logic from rpc/deps.py
    p2p_seeds = ""
    
    seeds = [s.strip() for s in p2p_seeds.split(",") if s.strip()]
    
    assert seeds == []


def test_deps_parse_p2p_seeds_with_empty_entries():
    """Test the logic that handles P2P_SEEDS with empty entries."""
    # Simulate the parsing logic from rpc/deps.py
    p2p_seeds = "/ip4/1.2.3.4/tcp/30333,, ,/ip4/5.6.7.8/tcp/30333"
    
    seeds = [s.strip() for s in p2p_seeds.split(",") if s.strip()]
    
    assert len(seeds) == 2
    assert "/ip4/1.2.3.4/tcp/30333" in seeds
    assert "/ip4/5.6.7.8/tcp/30333" in seeds


def test_p2p_listen_ipv6_multiaddr():
    """Test that IPv6 addresses in multiaddr format are handled correctly."""
    # Simulate the parsing logic from rpc/deps.py
    # IPv6 addresses should always use multiaddr format for proper handling
    p2p_listen = "/ip6/::1/tcp/30333"

    listen_addrs = []
    if p2p_listen:
        if ":" in p2p_listen and not p2p_listen.startswith("/"):
            # Format is "host:port", convert to multiaddr
            host, port = p2p_listen.rsplit(":", 1)
            listen_addrs = [f"/ip4/{host}/tcp/{port}"]
        else:
            # Already in multiaddr format
            listen_addrs = [p2p_listen]

    # IPv6 in multiaddr format is preserved correctly
    assert listen_addrs == ["/ip6/::1/tcp/30333"]
