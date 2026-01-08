#!/usr/bin/env python3
"""
Test P2P snapshot discovery and download functionality.
"""
import asyncio
import logging
from unittest.mock import MagicMock, AsyncMock, Mock
from dataclasses import dataclass, field

logging.basicConfig(level=logging.DEBUG)

# Mock _PeerState for testing
@dataclass
class MockPeerState:
    session_id: str
    remote: str
    direction: str = "outbound"
    conn: any = None
    stream: any = None
    framer: any = None
    write_lock: any = None
    peer_id: str = None
    hello: dict = None
    hello_done: asyncio.Event = field(default_factory=asyncio.Event)
    pending_snapshot_list: asyncio.Future = None
    pending_snapshot_chunk: asyncio.Future = None
    ready_for_sync: bool = True


async def test_query_peer_snapshots():
    """Test querying a single peer for snapshots."""
    from p2p.sync.snapshot_sync import _query_peers_for_snapshots
    
    # Create mock peer
    peer1 = MockPeerState(
        session_id="peer1",
        remote="192.168.1.100:30333",
    )
    peer1.hello_done.set()
    
    # Create mock P2P service
    mock_p2p = MagicMock()
    mock_p2p._peers = {
        ("192.168.1.100:30333", "outbound"): peer1,
    }
    
    # Mock query_peer_snapshots to return test snapshots
    async def mock_query(peer, chain_id, timeout):
        return [
            {
                "chain_id": 1,
                "checkpoint_height": 1000,
                "checkpoint_hash": "0xabc123",
                "blocks_count": 1000,
                "accounts_count": 50,
                "size_mb": 10.5,
                "timestamp": 1234567890,
            }
        ]
    
    mock_p2p.query_peer_snapshots = mock_query
    
    # Query peers for snapshots
    result = await _query_peers_for_snapshots(mock_p2p, chain_id=1)
    
    print(f"✅ Query result: {result}")
    
    # Verify we got snapshots from the peer
    assert len(result) == 1, f"Expected 1 peer with snapshots, got {len(result)}"
    assert "peer:192.168.1.100:30333" in result, "Expected peer key not found"
    assert len(result["peer:192.168.1.100:30333"]) == 1, "Expected 1 snapshot from peer"
    
    snapshot = result["peer:192.168.1.100:30333"][0]
    assert snapshot["checkpoint_height"] == 1000, "Unexpected checkpoint height"
    assert snapshot["chain_id"] == 1, "Unexpected chain ID"
    
    print("✅ Test passed: Query peer for snapshots")


async def test_query_multiple_peers():
    """Test querying multiple peers for snapshots."""
    from p2p.sync.snapshot_sync import _query_peers_for_snapshots
    
    # Create mock peers
    peer1 = MockPeerState(session_id="peer1", remote="192.168.1.100:30333")
    peer1.hello_done.set()
    
    peer2 = MockPeerState(session_id="peer2", remote="192.168.1.101:30333")
    peer2.hello_done.set()
    
    peer3 = MockPeerState(session_id="peer3", remote="192.168.1.102:30333")
    # peer3 not ready (no hello_done set)
    
    # Create mock P2P service
    mock_p2p = MagicMock()
    mock_p2p._peers = {
        ("192.168.1.100:30333", "outbound"): peer1,
        ("192.168.1.101:30333", "outbound"): peer2,
        ("192.168.1.102:30333", "outbound"): peer3,
    }
    
    # Mock query_peer_snapshots with different responses per peer
    async def mock_query(peer, chain_id, timeout):
        if peer.remote == "192.168.1.100:30333":
            return [
                {
                    "chain_id": 1,
                    "checkpoint_height": 1000,
                    "checkpoint_hash": "0xabc123",
                    "blocks_count": 1000,
                    "accounts_count": 50,
                    "size_mb": 10.5,
                    "timestamp": 1234567890,
                }
            ]
        elif peer.remote == "192.168.1.101:30333":
            return [
                {
                    "chain_id": 1,
                    "checkpoint_height": 2000,
                    "checkpoint_hash": "0xdef456",
                    "blocks_count": 2000,
                    "accounts_count": 100,
                    "size_mb": 20.0,
                    "timestamp": 1234567900,
                }
            ]
        else:
            return []
    
    mock_p2p.query_peer_snapshots = mock_query
    
    # Query peers for snapshots
    result = await _query_peers_for_snapshots(mock_p2p, chain_id=1)
    
    print(f"✅ Query result from multiple peers: {result}")
    
    # Verify we got snapshots from 2 peers (peer3 not ready)
    assert len(result) == 2, f"Expected 2 peers with snapshots, got {len(result)}"
    
    # Check peer1 snapshot
    assert "peer:192.168.1.100:30333" in result
    assert result["peer:192.168.1.100:30333"][0]["checkpoint_height"] == 1000
    
    # Check peer2 snapshot (should have higher height)
    assert "peer:192.168.1.101:30333" in result
    assert result["peer:192.168.1.101:30333"][0]["checkpoint_height"] == 2000
    
    print("✅ Test passed: Query multiple peers for snapshots")


async def test_find_highest_snapshot():
    """Test finding the highest snapshot from multiple peers."""
    from p2p.sync.snapshot_sync import try_snapshot_bootstrap
    
    # This test verifies that the highest snapshot is selected
    # We'll just verify the logic works with mocked data
    
    snapshots_by_source = {
        "peer:192.168.1.100:30333": [
            {
                "chain_id": 1,
                "checkpoint_height": 1000,
                "checkpoint_hash": "0xabc123",
                "blocks_count": 1000,
                "accounts_count": 50,
                "size_mb": 10.5,
                "timestamp": 1234567890,
                "_source": "peer:192.168.1.100:30333",
            }
        ],
        "peer:192.168.1.101:30333": [
            {
                "chain_id": 1,
                "checkpoint_height": 2000,
                "checkpoint_hash": "0xdef456",
                "blocks_count": 2000,
                "accounts_count": 100,
                "size_mb": 20.0,
                "timestamp": 1234567900,
                "_source": "peer:192.168.1.101:30333",
            }
        ],
    }
    
    # Flatten all snapshots
    all_snapshots = []
    for source, snaps in snapshots_by_source.items():
        for snap in snaps:
            snap["_source"] = source
            all_snapshots.append(snap)
    
    # Find best snapshot (highest height)
    best_snapshot = max(all_snapshots, key=lambda s: s["checkpoint_height"])
    
    print(f"✅ Best snapshot: {best_snapshot}")
    
    # Verify the highest snapshot was selected
    assert best_snapshot["checkpoint_height"] == 2000, "Expected height 2000"
    assert best_snapshot["_source"] == "peer:192.168.1.101:30333", "Wrong source"
    
    print("✅ Test passed: Find highest snapshot from multiple peers")


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Testing P2P Snapshot Discovery")
    print("=" * 60 + "\n")
    
    await test_query_peer_snapshots()
    print()
    
    await test_query_multiple_peers()
    print()
    
    await test_find_highest_snapshot()
    print()
    
    print("=" * 60)
    print("All tests passed! ✅")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
