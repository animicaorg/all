"""
Unit test for P2P2 compatibility wrapper.
"""

import pytest
from unittest.mock import Mock, MagicMock
from p2p2.compat import P2PService


def test_p2p2_compat_init():
    """Test that P2P2 compat wrapper initializes correctly."""
    # Mock dependencies
    mock_deps = Mock()
    mock_block_db = Mock()
    mock_state_db = Mock()
    mock_deps.block_db = mock_block_db
    mock_deps.state_db = mock_state_db
    
    # Mock genesis header
    mock_genesis_header = Mock()
    mock_genesis_header.hash.return_value = bytes.fromhex("0" * 64)
    mock_block_db.get_header = Mock(return_value=mock_genesis_header)
    
    # Create service
    service = P2PService(
        chain_id=1337,
        deps=mock_deps,
        peerstore_path="/tmp/test_peerstore",
        listen_addrs=["/ip4/127.0.0.1/tcp/30333"],
        seeds=["test_seed"],
    )
    
    # Verify attributes
    assert service.chain_id == 1337
    assert service.deps == mock_deps
    assert service._seeds == ["test_seed"]
    assert service._core_service is not None
    assert not service._running


def test_p2p2_compat_multiaddr_parsing():
    """Test that multiaddr parsing works correctly."""
    mock_deps = Mock()
    mock_deps.block_db = None
    mock_deps.state_db = None
    
    # Test different multiaddr formats
    test_cases = [
        ("/ip4/0.0.0.0/tcp/30333", "0.0.0.0", 30333),
        ("/ip4/192.168.1.1/tcp/9000", "192.168.1.1", 9000),
        ("/ip6/::1/tcp/8080", "::1", 8080),
        ("/dns4/example.com/tcp/1234", "example.com", 1234),
    ]
    
    for multiaddr, expected_host, expected_port in test_cases:
        service = P2PService(
            chain_id=1,
            deps=mock_deps,
            listen_addrs=[multiaddr],
        )
        
        # Check that the core service was initialized with correct host/port
        assert service._core_service.listen_host == expected_host
        assert service._core_service.listen_port == expected_port


def test_p2p2_compat_network_mapping():
    """Test that chain IDs map to correct network names."""
    mock_deps = Mock()
    mock_deps.block_db = None
    mock_deps.state_db = None
    
    test_cases = [
        (1, "mainnet"),
        (2, "testnet"),
        (1337, "devnet"),
        (999, "chain-999"),  # Unknown chain ID
    ]
    
    for chain_id, expected_network in test_cases:
        service = P2PService(
            chain_id=chain_id,
            deps=mock_deps,
        )
        
        assert service._core_service.network_id == expected_network


def test_p2p2_compat_methods():
    """Test that compat wrapper methods exist and have correct signatures."""
    mock_deps = Mock()
    mock_deps.block_db = None
    mock_deps.state_db = None
    
    service = P2PService(
        chain_id=1337,
        deps=mock_deps,
    )
    
    # Check that expected methods exist
    assert hasattr(service, "start")
    assert hasattr(service, "stop")
    assert hasattr(service, "get_peer_count")
    assert hasattr(service, "get_peers")
    assert hasattr(service, "connect_peer")
    assert hasattr(service, "disconnect_peer")
    
    # Check that methods are callable
    assert callable(service.start)
    assert callable(service.stop)
    assert callable(service.get_peer_count)
    assert callable(service.get_peers)
    assert callable(service.connect_peer)
    assert callable(service.disconnect_peer)


def test_p2p2_compat_get_peer_count_when_not_running():
    """Test that get_peer_count returns 0 when service is not running."""
    mock_deps = Mock()
    mock_deps.block_db = None
    mock_deps.state_db = None
    
    service = P2PService(
        chain_id=1337,
        deps=mock_deps,
    )
    
    assert service.get_peer_count() == 0


def test_p2p2_compat_get_peers_when_not_running():
    """Test that get_peers returns empty list when service is not running."""
    mock_deps = Mock()
    mock_deps.block_db = None
    mock_deps.state_db = None
    
    service = P2PService(
        chain_id=1337,
        deps=mock_deps,
    )
    
    assert service.get_peers() == []


if __name__ == "__main__":
    # Run tests
    test_p2p2_compat_init()
    print("✓ test_p2p2_compat_init passed")
    
    test_p2p2_compat_multiaddr_parsing()
    print("✓ test_p2p2_compat_multiaddr_parsing passed")
    
    test_p2p2_compat_network_mapping()
    print("✓ test_p2p2_compat_network_mapping passed")
    
    test_p2p2_compat_methods()
    print("✓ test_p2p2_compat_methods passed")
    
    test_p2p2_compat_get_peer_count_when_not_running()
    print("✓ test_p2p2_compat_get_peer_count_when_not_running passed")
    
    test_p2p2_compat_get_peers_when_not_running()
    print("✓ test_p2p2_compat_get_peers_when_not_running passed")
    
    print("\nAll tests passed! ✓")
