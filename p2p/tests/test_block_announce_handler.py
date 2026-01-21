"""
Tests for BlockAnnounceHandler (Phase 6).

These tests verify:
- HEAD_STATUS message handling
- Peer tip updates
- Sync triggering when peers are ahead
- Broadcasting of HEAD_STATUS on new blocks
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import AsyncMock, Mock, patch

import pytest


@dataclass
class MockFrame:
    """Mock frame for testing."""
    msg_id: int
    seq: int
    flags: int
    payload: bytes


@dataclass
class MockConn:
    """Mock connection for testing."""
    remote_addr: str
    _closed: bool = False
    
    def is_closed(self) -> bool:
        return self._closed
    
    async def send_frame(self, msg_id: int, payload: bytes, **kwargs) -> None:
        pass


@dataclass
class MockDeps:
    """Mock NodeDeps for testing."""
    chain_id: int = 1337
    
    async def get_canonical_height(self) -> int:
        return 100
    
    async def get_canonical_head(self) -> tuple:
        return (100, b"\x01" * 32)
    
    @property
    def head_reader(self):
        return self


@dataclass
class MockCodec:
    """Mock codec for testing."""
    
    def encode(self, msg: Any) -> bytes:
        return b"encoded"
    
    def decode(self, payload: bytes, msg_type: Any) -> Any:
        # Return a mock HeadStatus message
        return Mock(
            head_height=150,
            head_hash=b"\x02" * 32,
            timestamp_ms=int(time.time() * 1000),
            chain_id=1337,
        )


@dataclass
class MockTipManager:
    """Mock TipManager for testing."""
    updates: list = None
    
    def __post_init__(self):
        if self.updates is None:
            self.updates = []
    
    def on_tip_received(self, session_id: str, height: int, hash_hex: Optional[str] = None, tip_time: Optional[float] = None):
        self.updates.append({
            "session_id": session_id,
            "height": height,
            "hash_hex": hash_hex,
            "tip_time": tip_time,
        })


@dataclass
class MockRegistry:
    """Mock PeerRegistry for testing."""
    _sessions: dict = None
    
    def __post_init__(self):
        if self._sessions is None:
            self._sessions = {}


@dataclass
class MockEvents:
    """Mock EventBus for testing."""
    emitted: list = None
    subscriptions: list = None
    
    def __post_init__(self):
        if self.emitted is None:
            self.emitted = []
        if self.subscriptions is None:
            self.subscriptions = []
    
    async def emit(self, topic: str, data: Any):
        self.emitted.append({"topic": topic, "data": data})
    
    def subscribe(self, topic: str):
        sub = MockSubscription(topic)
        self.subscriptions.append(sub)
        return sub


@dataclass
class MockSubscription:
    """Mock subscription for testing."""
    topic: str
    events: list = None
    
    def __post_init__(self):
        if self.events is None:
            self.events = []
    
    def __aiter__(self):
        return self
    
    async def __anext__(self):
        if not self.events:
            await asyncio.sleep(0.1)
            raise StopAsyncIteration
        return self.events.pop(0)


@dataclass
class MockGossip:
    """Mock GossipEngine for testing."""
    published: list = None
    
    def __post_init__(self):
        if self.published is None:
            self.published = []
    
    async def publish(self, topic: str, payload: bytes):
        self.published.append({"topic": topic, "payload": payload})


class TestBlockAnnounceHandler:
    """Test suite for BlockAnnounceHandler."""
    
    @pytest.fixture
    def handler(self):
        """Create a handler instance for testing."""
        from p2p.protocol.block_announce_handler import BlockAnnounceHandler
        
        cfg = Mock()
        codec = MockCodec()
        deps = MockDeps()
        gossip = MockGossip()
        tip_manager = MockTipManager()
        registry = MockRegistry()
        events = MockEvents()
        
        handler = BlockAnnounceHandler(
            cfg=cfg,
            codec=codec,
            deps=deps,
            gossip=gossip,
            tip_manager=tip_manager,
            registry=registry,
            events=events,
        )
        
        return handler
    
    @pytest.mark.asyncio
    async def test_handler_initialization(self, handler):
        """Test that handler initializes correctly."""
        assert handler._metrics["announcements_sent"] == 0
        assert handler._metrics["announcements_received"] == 0
        assert handler._metrics["peer_tips_updated"] == 0
        assert handler._metrics["sync_triggered"] == 0
    
    @pytest.mark.asyncio
    async def test_msg_ids(self, handler):
        """Test that handler registers correct message IDs."""
        from p2p.wire.message_ids import MsgID
        
        msg_ids = list(handler.msg_ids())
        assert MsgID.HEAD_STATUS in msg_ids
    
    @pytest.mark.asyncio
    async def test_handle_head_status(self, handler):
        """Test handling of incoming HEAD_STATUS message."""
        from p2p.wire.message_ids import MsgID
        
        # Create mock connection and frame
        conn = MockConn(remote_addr="tcp://127.0.0.1:9001")
        frame = MockFrame(
            msg_id=MsgID.HEAD_STATUS,
            seq=1,
            flags=0,
            payload=b"mock_payload",
        )
        
        # Handle the message
        await handler.handle(conn, frame)
        
        # Verify metrics
        assert handler._metrics["announcements_received"] == 1
        assert handler._metrics["peer_tips_updated"] == 1
        
        # Verify tip manager was updated
        assert len(handler.tip_manager.updates) == 1
        update = handler.tip_manager.updates[0]
        assert update["height"] == 150  # From MockCodec decode
        assert update["hash_hex"] is not None
    
    @pytest.mark.asyncio
    async def test_handle_triggers_sync_when_behind(self, handler):
        """Test that handler triggers sync when peer is ahead."""
        from p2p.wire.message_ids import MsgID
        
        # Peer is at height 150, local is at 100 (gap > 2)
        conn = MockConn(remote_addr="tcp://127.0.0.1:9001")
        frame = MockFrame(
            msg_id=MsgID.HEAD_STATUS,
            seq=1,
            flags=0,
            payload=b"mock_payload",
        )
        
        # Handle the message
        await handler.handle(conn, frame)
        
        # Verify sync was triggered
        assert handler._metrics["sync_triggered"] == 1
        
        # Verify event was emitted
        assert len(handler.events.emitted) == 1
        event = handler.events.emitted[0]
        assert event["topic"] == "syncCheck"
        assert event["data"]["reason"] == "peer_ahead"
        assert event["data"]["local_height"] == 100
        assert event["data"]["peer_height"] == 150
    
    @pytest.mark.asyncio
    async def test_broadcast_head_status(self, handler):
        """Test broadcasting HEAD_STATUS announcement."""
        # Mock local head
        handler.deps.head_height = 100
        handler.deps.head_hash = b"\x01" * 32
        
        # Broadcast
        await handler._broadcast_head_status()
        
        # Verify metrics
        assert handler._metrics["announcements_sent"] == 1
        
        # Verify gossip was used
        assert len(handler.gossip.published) == 1
        pub = handler.gossip.published[0]
        assert "blocks" in pub["topic"].lower() or "chain_id" in pub["topic"]
        assert pub["payload"] == b"encoded"
    
    @pytest.mark.asyncio
    async def test_start_subscribes_to_events(self, handler):
        """Test that start() subscribes to newHead events."""
        await handler.start()
        
        assert handler._subscribed is True
        assert len(handler.events.subscriptions) == 1
        assert handler.events.subscriptions[0].topic == "newHead"
    
    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self, handler):
        """Test that stop() cancels background tasks."""
        await handler.start()
        assert handler._broadcast_task is not None
        
        await handler.stop()
        assert handler._subscribed is False
        assert handler._broadcast_task is None or handler._broadcast_task.cancelled()
    
    @pytest.mark.asyncio
    async def test_get_local_height(self, handler):
        """Test getting local chain height."""
        height = await handler._get_local_height()
        assert height == 100
    
    @pytest.mark.asyncio
    async def test_get_local_head(self, handler):
        """Test getting local chain head."""
        height, hash_ = await handler._get_local_head()
        assert height == 100
        assert hash_ == b"\x01" * 32
    
    @pytest.mark.asyncio
    async def test_get_metrics(self, handler):
        """Test getting metrics snapshot."""
        metrics = handler.get_metrics()
        assert "announcements_sent" in metrics
        assert "announcements_received" in metrics
        assert "peer_tips_updated" in metrics
        assert "sync_triggered" in metrics


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
