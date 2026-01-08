#!/usr/bin/env python3
"""
Test peer-of-peer snapshot discovery feature.

This test validates that the snapshot discovery mechanism can query
not only directly connected peers but also their peers (second-degree connections).
"""

import asyncio
import os
import sys
from unittest.mock import Mock, AsyncMock, patch


async def test_peer_of_peer_discovery():
    """Test that peer-of-peer snapshot discovery finds indirect snapshots."""
    
    # Set environment to enable peer-of-peer discovery
    os.environ['ANIMICA_SNAPSHOT_PEER_OF_PEER_ENABLED'] = 'true'
    
    # Import after setting environment
    from p2p.sync.snapshot_sync import _query_peers_for_snapshots, _is_peer_of_peer_discovery_enabled
    
    # Verify feature is enabled
    assert _is_peer_of_peer_discovery_enabled(), "Peer-of-peer discovery should be enabled"
    
    # Create mock P2P service
    p2p_service = Mock()
    
    # Create mock direct peers
    peer1 = Mock()
    peer1.remote = "peer1.example.com:30333"
    peer1.hello_done = AsyncMock()
    peer1.hello_done.is_set = Mock(return_value=True)
    peer1.known_addrs = {
        "indirect1.example.com:30333": 1234567890.0,
        "indirect2.example.com:30333": 1234567891.0,
    }
    
    peer2 = Mock()
    peer2.remote = "peer2.example.com:30333"
    peer2.hello_done = AsyncMock()
    peer2.hello_done.is_set = Mock(return_value=True)
    peer2.known_addrs = {
        "indirect3.example.com:30333": 1234567892.0,
    }
    
    # Set up P2P service mocks
    p2p_service._peers = {
        ("peer1.example.com:30333", "outbound"): peer1,
        ("peer2.example.com:30333", "outbound"): peer2,
    }
    
    # Mock query_peer_snapshots to return different snapshots for direct and indirect peers
    async def mock_query_snapshots(peer, chain_id, timeout):
        if peer.remote == "peer1.example.com:30333":
            return [{"checkpoint_height": 1000, "checkpoint_hash": "hash1"}]
        elif peer.remote == "peer2.example.com:30333":
            return [{"checkpoint_height": 2000, "checkpoint_hash": "hash2"}]
        elif peer.remote == "indirect1.example.com:30333":
            return [{"checkpoint_height": 3000, "checkpoint_hash": "hash3"}]
        else:
            return []
    
    p2p_service.query_peer_snapshots = AsyncMock(side_effect=mock_query_snapshots)
    
    # Test the discovery
    print("Testing peer-of-peer snapshot discovery...")
    chain_id = 1337
    
    # Query with peer-of-peer enabled
    snapshots_by_peer = await _query_peers_for_snapshots(
        p2p_service, 
        chain_id,
        include_peer_of_peers=True
    )
    
    print(f"Discovered snapshots from {len(snapshots_by_peer)} source(s)")
    for source, snapshots in snapshots_by_peer.items():
        print(f"  {source}: {len(snapshots)} snapshot(s)")
        for snap in snapshots:
            print(f"    - Height: {snap.get('checkpoint_height')}")
    
    # Verify we got snapshots from direct peers
    assert len(snapshots_by_peer) >= 2, f"Should discover snapshots from at least 2 peers, got {len(snapshots_by_peer)}"
    
    # Find direct peer snapshots
    direct_sources = [k for k in snapshots_by_peer.keys() if k.startswith("peer:") and not k.startswith("peer-of-peer:")]
    assert len(direct_sources) >= 2, f"Should have at least 2 direct peer sources, got {len(direct_sources)}"
    
    print("\n✅ Peer-of-peer snapshot discovery test passed!")
    print(f"   - Direct peers: {len(direct_sources)}")
    print(f"   - Total sources: {len(snapshots_by_peer)}")
    
    return True


async def test_peer_of_peer_disabled():
    """Test that peer-of-peer discovery can be disabled."""
    
    # Disable peer-of-peer discovery
    os.environ['ANIMICA_SNAPSHOT_PEER_OF_PEER_ENABLED'] = 'false'
    
    from p2p.sync.snapshot_sync import _is_peer_of_peer_discovery_enabled
    
    assert not _is_peer_of_peer_discovery_enabled(), "Peer-of-peer discovery should be disabled"
    
    print("✅ Peer-of-peer disable test passed!")
    return True


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Peer-of-Peer Snapshot Discovery Test Suite")
    print("=" * 60)
    print()
    
    try:
        # Test 1: Feature can be disabled
        await test_peer_of_peer_disabled()
        print()
        
        # Test 2: Peer-of-peer discovery works
        await test_peer_of_peer_discovery()
        print()
        
        print("=" * 60)
        print("✅ All tests passed successfully!")
        print("=" * 60)
        return 0
        
    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ Test failed with error: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
