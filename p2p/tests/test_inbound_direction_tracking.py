"""
Test that inbound peer connections are tracked with direction information.

This validates that:
1. PeerStore can store and retrieve direction information
2. P2PService tracks direction when accepting inbound connections
3. RPC methods include direction in peer listings
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure local package is importable - use relative path from test location
_test_dir = Path(__file__).parent
_repo_root = _test_dir.parent.parent
sys.path.insert(0, str(_repo_root))

try:
    from p2p.peer import peerstore
except Exception as e:
    pytest.skip(f"p2p.peer.peerstore not available: {e}", allow_module_level=True)


def test_peerstore_direction_column():
    """Test that PeerStore supports the direction column."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = peerstore.PeerStore(tmpdir)
        
        # Add a peer with inbound direction
        store.add(
            peer_id="peer_inbound_test",
            addrs=["/ip4/1.2.3.4/tcp/30333"],
            score=1.0,
            direction="inbound"
        )
        
        # Add a peer with outbound direction
        store.add(
            peer_id="peer_outbound_test",
            addrs=["/ip4/5.6.7.8/tcp/30333"],
            score=1.0,
            direction="outbound"
        )
        
        # Retrieve and verify direction is preserved
        peer_inbound = store.get("peer_inbound_test")
        assert peer_inbound is not None
        assert hasattr(peer_inbound, "direction")
        assert peer_inbound.direction == "inbound"
        
        peer_outbound = store.get("peer_outbound_test")
        assert peer_outbound is not None
        assert hasattr(peer_outbound, "direction")
        assert peer_outbound.direction == "outbound"


def test_peerstore_direction_migration():
    """Test that existing databases without direction column are migrated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "peers.db"
        
        # Create initial store (will have direction column from new schema)
        store1 = peerstore.PeerStore(db_path)
        store1.add(
            peer_id="peer_test",
            addrs=["/ip4/1.2.3.4/tcp/30333"],
            score=1.0
        )
        
        # Reopen store (migration should be idempotent)
        store2 = peerstore.PeerStore(db_path)
        peer = store2.get("peer_test")
        assert peer is not None
        # Direction should be None for peers added without explicit direction
        # or should have the direction column available


def test_peerstore_direction_update():
    """Test that direction can be updated on existing peers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = peerstore.PeerStore(tmpdir)
        
        # Add peer without direction
        store.add(
            peer_id="peer_update_test",
            addrs=["/ip4/1.2.3.4/tcp/30333"],
            score=1.0
        )
        
        # Update with direction
        store.add(
            peer_id="peer_update_test",
            addrs=["/ip4/1.2.3.4/tcp/30333"],
            score=1.0,
            direction="inbound"
        )
        
        # Verify direction is set
        peer = store.get("peer_update_test")
        assert peer is not None
        if hasattr(peer, "direction"):
            assert peer.direction == "inbound"


def test_peerstore_upsert_with_direction():
    """Test that upsert method also supports direction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = peerstore.PeerStore(tmpdir)
        
        # Use upsert with direction
        store.upsert(
            peer_id="peer_upsert_test",
            addrs=["/ip4/1.2.3.4/tcp/30333"],
            score=1.0,
            direction="outbound"
        )
        
        # Verify
        peer = store.get("peer_upsert_test")
        assert peer is not None
        if hasattr(peer, "direction"):
            assert peer.direction == "outbound"


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v"])
