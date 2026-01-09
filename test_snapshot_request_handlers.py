#!/usr/bin/env python3
"""
Test that P2P service correctly handles GET_SNAPSHOTS and GET_SNAPSHOT_CHUNK requests.

This tests the fix for snapshot propagation issue where nodes couldn't respond
to snapshot requests from peers.
"""
import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


@dataclass
class MockPeerState:
    """Mock peer state for testing."""
    session_id: str
    remote: str
    direction: str = "outbound"
    conn: any = None
    stream: any = None
    framer: any = None
    write_lock: any = field(default_factory=asyncio.Lock)
    peer_id: str = None
    hello: dict = None
    hello_done: asyncio.Event = field(default_factory=asyncio.Event)
    pending_snapshot_list: asyncio.Future = None
    pending_snapshot_chunk: asyncio.Future = None
    ready_for_sync: bool = True


def create_test_snapshot(snapshots_dir: Path, chain_id: int, height: int):
    """Create a test snapshot directory with manifest and chunks."""
    snapshot_dir = snapshots_dir / f"chain-{chain_id}-height-{height}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    
    # Create manifest
    manifest = {
        "chain_id": chain_id,
        "checkpoint_height": height,
        "checkpoint_hash": f"0x{'00' * 32}",
        "blocks_count": height,
        "accounts_count": 10,
        "storage_keys_count": 20,
        "timestamp": 1234567890,
        "chunks": [
            {"name": "blocks.tar.zst", "size": 1024, "hash": "abc123"},
            {"name": "state.tar.zst", "size": 2048, "hash": "def456"},
        ],
    }
    
    with open(snapshot_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    
    # Create dummy chunk files
    with open(snapshot_dir / "blocks.tar.zst", "wb") as f:
        f.write(b"fake blocks data")
    
    with open(snapshot_dir / "state.tar.zst", "wb") as f:
        f.write(b"fake state data")
    
    log.info(f"Created test snapshot at {snapshot_dir}")
    return snapshot_dir


async def test_handle_get_snapshots():
    """Test that _handle_get_snapshots responds correctly."""
    from p2p.node.p2p_service import P2PService
    from p2p.wire.messages import GetSnapshots
    from p2p.wire.encoding import encode_payload
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create test snapshots
        chain_data_dir = tmpdir / "chain-1"
        chain_data_dir.mkdir()
        snapshots_dir = tmpdir / "snapshots"
        snapshots_dir.mkdir()
        
        create_test_snapshot(snapshots_dir, chain_id=1, height=1000)
        create_test_snapshot(snapshots_dir, chain_id=1, height=2000)
        create_test_snapshot(snapshots_dir, chain_id=2, height=1000)
        
        # Create mock deps
        mock_deps = Mock()
        mock_deps.block_db = Mock()
        mock_deps.block_db.get_genesis_hash = Mock(return_value=b"0" * 32)
        
        # Create P2P service instance
        service = P2PService(
            listen_addrs=["/ip4/127.0.0.1/tcp/30333"],
            chain_id=1,
            deps=mock_deps,
        )
        
        # Override chain_data_dir to use our test directory
        service._chain_data_dir = tmpdir
        
        # Create mock peer
        peer = MockPeerState(
            session_id="test_peer",
            remote="127.0.0.1:30333"
        )
        
        # Track sent messages
        sent_messages = []
        
        async def mock_send(peer, msg_id, msg):
            sent_messages.append((msg_id, msg))
        
        service._send = mock_send
        service._decode_map = lambda payload: {"chain_id": 1}
        
        # Create request payload
        request = GetSnapshots(chain_id=1)
        payload = encode_payload(request)
        
        # Call handler
        await service._handle_get_snapshots(peer, payload)
        
        # Verify response was sent
        assert len(sent_messages) == 1, f"Expected 1 message sent, got {len(sent_messages)}"
        
        msg_id, response = sent_messages[0]
        from p2p.wire.message_ids import MsgID
        assert msg_id == MsgID.SNAPSHOTS, f"Expected SNAPSHOTS message, got {msg_id}"
        
        # Verify response contains snapshots
        assert hasattr(response, 'snapshots'), "Response should have snapshots field"
        snapshots = response.snapshots
        
        # Should have 2 snapshots for chain_id=1
        assert len(snapshots) == 2, f"Expected 2 snapshots, got {len(snapshots)}"
        
        # Verify snapshots are sorted by height (descending)
        assert snapshots[0].checkpoint_height == 2000, "First snapshot should be at height 2000"
        assert snapshots[1].checkpoint_height == 1000, "Second snapshot should be at height 1000"
        
        log.info("✅ test_handle_get_snapshots passed")


async def test_handle_get_snapshot_chunk():
    """Test that _handle_get_snapshot_chunk responds correctly."""
    from p2p.node.p2p_service import P2PService
    from p2p.wire.messages import GetSnapshotChunk
    from p2p.wire.encoding import encode_payload
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create test snapshot
        chain_data_dir = tmpdir / "chain-1"
        chain_data_dir.mkdir()
        snapshots_dir = tmpdir / "snapshots"
        snapshots_dir.mkdir()
        
        create_test_snapshot(snapshots_dir, chain_id=1, height=1000)
        
        # Create mock deps
        mock_deps = Mock()
        mock_deps.block_db = Mock()
        mock_deps.block_db.get_genesis_hash = Mock(return_value=b"0" * 32)
        
        # Create P2P service instance
        service = P2PService(
            listen_addrs=["/ip4/127.0.0.1/tcp/30333"],
            chain_id=1,
            deps=mock_deps,
        )
        
        # Override chain_data_dir to use our test directory
        service._chain_data_dir = tmpdir
        
        # Create mock peer
        peer = MockPeerState(
            session_id="test_peer",
            remote="127.0.0.1:30333"
        )
        
        # Track sent messages
        sent_messages = []
        
        async def mock_send(peer, msg_id, msg):
            sent_messages.append((msg_id, msg))
        
        service._send = mock_send
        service._decode_map = lambda payload: {
            "chain_id": 1,
            "checkpoint_height": 1000,
            "chunk_name": "blocks.tar.zst"
        }
        
        # Create request payload
        request = GetSnapshotChunk(
            chain_id=1,
            checkpoint_height=1000,
            chunk_name="blocks.tar.zst"
        )
        payload = encode_payload(request)
        
        # Call handler
        await service._handle_get_snapshot_chunk(peer, payload)
        
        # Verify response was sent
        assert len(sent_messages) == 1, f"Expected 1 message sent, got {len(sent_messages)}"
        
        msg_id, response = sent_messages[0]
        from p2p.wire.message_ids import MsgID
        assert msg_id == MsgID.SNAPSHOT_CHUNK, f"Expected SNAPSHOT_CHUNK message, got {msg_id}"
        
        # Verify response contains chunk data
        assert hasattr(response, 'data'), "Response should have data field"
        assert hasattr(response, 'found'), "Response should have found field"
        
        assert response.found == True, "Chunk should be found"
        assert len(response.data) > 0, "Chunk data should not be empty"
        assert response.data == b"fake blocks data", "Chunk data should match"
        
        log.info("✅ test_handle_get_snapshot_chunk passed")


async def test_list_local_snapshots():
    """Test that _list_local_snapshots correctly scans snapshot directory."""
    from p2p.node.p2p_service import P2PService
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create test snapshots
        snapshots_dir = tmpdir / "snapshots"
        snapshots_dir.mkdir()
        
        create_test_snapshot(snapshots_dir, chain_id=1, height=1000)
        create_test_snapshot(snapshots_dir, chain_id=1, height=2000)
        create_test_snapshot(snapshots_dir, chain_id=2, height=1500)
        
        # Create mock deps
        mock_deps = Mock()
        mock_deps.block_db = Mock()
        mock_deps.block_db.get_genesis_hash = Mock(return_value=b"0" * 32)
        
        # Create P2P service instance
        service = P2PService(
            listen_addrs=["/ip4/127.0.0.1/tcp/30333"],
            chain_id=1,
            deps=mock_deps,
        )
        
        # Override chain_data_dir to use our test directory
        service._chain_data_dir = tmpdir
        
        # Test listing all snapshots
        all_snapshots = service._list_local_snapshots()
        assert len(all_snapshots) == 3, f"Expected 3 snapshots, got {len(all_snapshots)}"
        
        # Test listing snapshots for chain_id=1
        chain1_snapshots = service._list_local_snapshots(chain_id=1)
        assert len(chain1_snapshots) == 2, f"Expected 2 snapshots for chain 1, got {len(chain1_snapshots)}"
        
        # Verify snapshots are sorted by height (descending)
        assert chain1_snapshots[0].checkpoint_height == 2000
        assert chain1_snapshots[1].checkpoint_height == 1000
        
        # Test listing snapshots for chain_id=2
        chain2_snapshots = service._list_local_snapshots(chain_id=2)
        assert len(chain2_snapshots) == 1, f"Expected 1 snapshot for chain 2, got {len(chain2_snapshots)}"
        assert chain2_snapshots[0].checkpoint_height == 1500
        
        log.info("✅ test_list_local_snapshots passed")


async def test_read_snapshot_chunk():
    """Test that _read_snapshot_chunk correctly reads chunk files."""
    from p2p.node.p2p_service import P2PService
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Create test snapshot
        snapshots_dir = tmpdir / "snapshots"
        snapshots_dir.mkdir()
        
        create_test_snapshot(snapshots_dir, chain_id=1, height=1000)
        
        # Create mock deps
        mock_deps = Mock()
        mock_deps.block_db = Mock()
        mock_deps.block_db.get_genesis_hash = Mock(return_value=b"0" * 32)
        
        # Create P2P service instance
        service = P2PService(
            listen_addrs=["/ip4/127.0.0.1/tcp/30333"],
            chain_id=1,
            deps=mock_deps,
        )
        
        # Override chain_data_dir to use our test directory
        service._chain_data_dir = tmpdir
        
        # Test reading existing chunk
        data, found = service._read_snapshot_chunk(1, 1000, "blocks.tar.zst")
        assert found == True, "Chunk should be found"
        assert data == b"fake blocks data", "Chunk data should match"
        
        # Test reading non-existent chunk
        data, found = service._read_snapshot_chunk(1, 1000, "nonexistent.tar.zst")
        assert found == False, "Non-existent chunk should not be found"
        assert data == b"", "Data should be empty"
        
        # Test reading from non-existent snapshot
        data, found = service._read_snapshot_chunk(1, 9999, "blocks.tar.zst")
        assert found == False, "Chunk from non-existent snapshot should not be found"
        assert data == b"", "Data should be empty"
        
        log.info("✅ test_read_snapshot_chunk passed")


async def main():
    """Run all tests."""
    log.info("=" * 60)
    log.info("Testing snapshot request handlers")
    log.info("=" * 60)
    
    try:
        await test_list_local_snapshots()
        await test_read_snapshot_chunk()
        await test_handle_get_snapshots()
        await test_handle_get_snapshot_chunk()
        
        log.info("=" * 60)
        log.info("✅ All tests passed!")
        log.info("=" * 60)
        return 0
    except AssertionError as e:
        log.error(f"❌ Test failed: {e}")
        return 1
    except Exception as e:
        log.error(f"❌ Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
