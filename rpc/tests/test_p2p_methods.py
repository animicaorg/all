"""Tests for P2P RPC methods."""

import pytest


def test_p2p_methods_registered():
    """Test that P2P methods are registered in the RPC registry."""
    from rpc.methods import ensure_loaded, get_methods
    
    # Ensure methods are loaded
    ensure_loaded()
    
    # Get the method registry
    methods = get_methods()
    
    # Check that p2p.listPeers and its aliases are registered
    assert "p2p.listPeers" in methods
    assert "p2p.getPeers" in methods
    assert "p2p.peers" in methods
    assert "admin_peers" in methods
    assert "net_peers" in methods
    
    # Check other P2P methods
    assert "p2p.addPeer" in methods
    assert "p2p.removePeer" in methods
    assert "p2p.getPeerInfo" in methods


@pytest.mark.asyncio
async def test_list_peers_no_service():
    """Test that list_peers returns empty list when P2P service is not available."""
    from rpc.methods.p2p import list_peers
    
    # Without a running P2P service, should return empty list
    result = await list_peers()
    
    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.asyncio
async def test_add_peer_no_service():
    """Test that add_peer returns error when P2P service is not available."""
    from rpc.methods.p2p import add_peer
    
    # Without a running P2P service, should return error
    result = await add_peer("/ip4/127.0.0.1/tcp/30303")
    
    assert isinstance(result, dict)
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_remove_peer_no_service():
    """Test that remove_peer returns error when P2P service is not available."""
    from rpc.methods.p2p import remove_peer
    
    # Without a running P2P service, should return error
    result = await remove_peer("peer12345")
    
    assert isinstance(result, dict)
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_get_peer_info_no_service():
    """Test that get_peer_info returns None when P2P service is not available."""
    from rpc.methods.p2p import get_peer_info
    
    # Without a running P2P service, should return None
    result = await get_peer_info("peer12345")
    
    assert result is None


def test_peer_to_dict_conversion():
    """Test _peer_to_dict helper function."""
    from dataclasses import dataclass
    from rpc.methods.p2p import _peer_to_dict
    
    # Test with dict input
    peer_dict = {"id": "peer123", "addr": "/ip4/1.2.3.4/tcp/30303", "status": "connected"}
    result = _peer_to_dict(peer_dict)
    assert result == peer_dict
    
    # Test with object input
    @dataclass
    class MockPeer:
        peer_id: str
        address: str
        status: str
        direction: str = "outbound"
        last_rtt_ms: float = 50.0
    
    peer_obj = MockPeer(
        peer_id="peer456",
        address="/ip4/5.6.7.8/tcp/30303",
        status="connected",
    )
    result = _peer_to_dict(peer_obj)
    
    assert result["id"] == "peer456"
    assert result["addr"] == "/ip4/5.6.7.8/tcp/30303"
    assert result["status"] == "connected"
    assert result["direction"] == "outbound"
    assert result["latencyMs"] == 50.0
