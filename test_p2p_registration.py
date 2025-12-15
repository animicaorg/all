"""
Test that P2P service registration works correctly.

This test validates the fix for the peer list CLI issue where the P2P service
wasn't being registered with the global registry, preventing RPC methods from
accessing it.
"""
import sys
import os

# Add the repo root to the path
sys.path.insert(0, os.path.dirname(__file__))


def test_p2p_register_service():
    """Test that p2p.register_service() and p2p.get_service() work correctly."""
    import p2p
    
    # Create a mock P2P service
    class MockP2PService:
        def __init__(self):
            self.started = False
            self.peers = {}
        
        async def start(self):
            self.started = True
        
        async def stop(self):
            self.started = False
    
    # Initially, no service should be registered
    assert p2p.get_service() is None
    
    # Register a service
    mock_service = MockP2PService()
    p2p.register_service(mock_service)
    
    # Now we should be able to get it
    retrieved = p2p.get_service()
    assert retrieved is mock_service
    assert retrieved.started is False
    
    print("✓ P2P service registration works correctly")


def test_p2p_get_connection_manager():
    """Test that p2p.get_connection_manager() returns None or the connection manager."""
    import p2p
    
    # Create a mock P2P service with a connection manager
    class MockConnectionManager:
        def __init__(self):
            self.peers_list = []
        
        def list_peers(self):
            return self.peers_list
    
    class MockP2PService:
        def __init__(self):
            self.connmgr = MockConnectionManager()
    
    # Register the service
    mock_service = MockP2PService()
    p2p.register_service(mock_service)
    
    # Get connection manager
    cm = p2p.get_connection_manager()
    assert cm is not None
    assert hasattr(cm, 'list_peers')
    assert cm.list_peers() == []
    
    print("✓ P2P get_connection_manager works correctly")


def test_rpc_methods_can_access_p2p():
    """Test that RPC methods can access the registered P2P service."""
    import p2p
    from rpc.methods.p2p import _get_p2p_service
    
    # Create a mock P2P service with peers property (like the lightweight P2PService)
    class MockP2PService:
        def __init__(self):
            self._peers = {
                "192.168.1.100:30303": {
                    "peer_id": "peer_abc123",
                    "remote": "192.168.1.100:30303",
                    "connected": True,
                    "direction": "outbound",
                    "last_seen": 1234567890.0,
                }
            }
        
        @property
        def peers(self):
            return self._peers
    
    # Register the service
    mock_service = MockP2PService()
    p2p.register_service(mock_service)
    
    # RPC method should be able to get the P2P service
    svc = _get_p2p_service()
    assert svc is not None
    assert hasattr(svc, 'peers')
    assert len(svc.peers) == 1
    
    print("✓ RPC methods can access P2P service via global registry")


if __name__ == "__main__":
    print("Testing P2P service registration...")
    test_p2p_register_service()
    test_p2p_get_connection_manager()
    test_rpc_methods_can_access_p2p()
    print("\n✅ All P2P registration tests passed!")
