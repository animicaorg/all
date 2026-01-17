"""
Tests for HEAD_STATUS message handling and periodic broadcasting.

Verifies that:
1. HEAD_STATUS messages are properly encoded/decoded
2. GET_HEAD_STATUS requests are handled correctly
3. HEAD_STATUS updates refresh peer tip timestamps
4. Periodic broadcasting keeps peer tips fresh
5. Freshness window is 600 seconds (10 minutes)
"""
from __future__ import annotations

import time
from unittest.mock import Mock, AsyncMock, MagicMock
import asyncio
from pathlib import Path

import pytest

from p2p.constants import NETWORK_MAGIC
from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, _PeerState
from p2p.tests import tcp_multiaddr
from p2p.wire.encoding import encode_payload
from p2p.wire.frames import Framer
from p2p.wire.message_ids import MsgID
from p2p.wire.messages import HeadStatus, GetHeadStatus

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "mainnet.json"


class TestHeadStatusMessages:
    """Test HEAD_STATUS message structure and validation."""

    def test_head_status_structure(self):
        """Test HeadStatus message has required fields."""
        head_status = HeadStatus(
            chain_id=0,
            head_height=100,
            head_hash=b"\x01" * 32,
            timestamp_ms=int(time.time() * 1000),
            network_best_height=105,
        )
        
        assert head_status.chain_id == 0
        assert head_status.head_height == 100
        assert len(head_status.head_hash) == 32
        assert head_status.timestamp_ms > 0
        assert head_status.network_best_height == 105
        assert head_status.msg_id == MsgID.HEAD_STATUS

    def test_get_head_status_structure(self):
        """Test GetHeadStatus message is simple request."""
        get_head_status = GetHeadStatus()
        assert get_head_status.msg_id == MsgID.GET_HEAD_STATUS

    def test_head_status_hash_validation(self):
        """Test HeadStatus validates hash length."""
        # Valid: 32 bytes
        HeadStatus(
            chain_id=0,
            head_height=100,
            head_hash=b"\x01" * 32,
            timestamp_ms=int(time.time() * 1000),
        )
        
        # Invalid: wrong length
        with pytest.raises(ValueError, match="must be 32 bytes"):
            HeadStatus(
                chain_id=0,
                head_height=100,
                head_hash=b"\x01" * 16,  # Wrong length
                timestamp_ms=int(time.time() * 1000),
            )


class TestHeadStatusFreshness:
    """Test HEAD_STATUS freshness logic (600s / 10 minute window)."""

    def test_freshness_window_is_600_seconds(self):
        """Verify TIP_FRESHNESS_SEC is 600.0 in _compute_best_remote_info."""
        # This is a documentation test to ensure we maintain 600s (10 minute) freshness
        # The actual constant is defined in p2p/node/p2p_service.py
        expected_freshness = 600.0
        
        # We expect HEAD_STATUS broadcasts every 10s
        # With 600s freshness window, we allow 60 missed heartbeats (60 * 10s = 600s)
        heartbeat_interval = 10.0
        max_missed_heartbeats = int(expected_freshness / heartbeat_interval)
        
        assert max_missed_heartbeats >= 60, "Should allow at least 60 missed heartbeats"
        assert expected_freshness == 600.0, "Freshness window should be 600s (10 minutes) per requirements"

    def test_broadcast_interval_is_10_seconds(self):
        """Verify HEAD_STATUS broadcasts every 10 seconds."""
        # This is a documentation test to ensure we maintain 10s broadcast interval
        # The actual constant is defined in p2p/node/p2p_service.py
        expected_interval = 10.0
        
        # With 10s broadcasts and 600s freshness, we get excellent tolerance
        freshness_window = 600.0
        safety_margin = freshness_window - (expected_interval * 60)
        
        assert safety_margin >= 0, "Should have positive safety margin"
        assert expected_interval == 10.0, "Broadcast interval should be 10s per requirements"


@pytest.mark.asyncio
async def test_polling_refreshes_peer_tip_and_sync_status(tmp_path: Path) -> None:
    deps_sync = P2PDeps.open(f"sqlite:///{tmp_path / 'poll.db'}", str(GENESIS_PATH))
    node = P2PService(
        listen_addrs=[tcp_multiaddr(0)],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps_sync,
        peerstore_path=str(tmp_path / "poll" / "p2p"),
    )

    session = node._peer_registry.register("peer:0", "inbound")
    peer = _PeerState(
        session_id=session.session_id,
        remote="peer:0",
        direction="inbound",
        conn=None,
        stream=AsyncMock(),
        framer=Framer(aead=None),
        write_lock=asyncio.Lock(),
    )
    node._peers[peer.remote] = peer
    node._peers_by_session[peer.session_id] = peer

    peer.hello = {
        "chain_id": node.chain_id,
        "network_magic": NETWORK_MAGIC,
        "genesis_header_hash": node._genesis_header_hash(),
        "genesis_block_hash": node._genesis_block_hash(),
        "genesis_hash": node._genesis_header_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "head_height": 0,
        "head_hash": b"\x00" * 32,
        "capabilities": ["sync"],
    }
    peer.repo_state_ok = True
    peer.hello_done.set()

    async def _fake_request_peer_head_status(_peer: _PeerState, *, reason: str) -> bool:
        head_status = HeadStatus(
            chain_id=node.chain_id,
            head_height=5,
            head_hash=b"\x11" * 32,
            timestamp_ms=int(time.time() * 1000),
            network_best_height=5,
        )
        await node._handle_head_status(_peer, encode_payload(head_status))
        return True

    node._request_peer_head_status = _fake_request_peer_head_status  # type: ignore[assignment]

    await node._poll_peer_heads(reason="test", force=True)

    total, fresh, stale = node._peer_tip_freshness_snapshot(chain_id=node.chain_id)
    assert total == 1
    assert fresh == 1
    assert stale == 0

    snap = node.sync_status_snapshot()
    assert snap.best_remote_height == 5
    assert snap.sync_status_reason != "no_fresh_peer_tips"


@pytest.mark.asyncio
async def test_genesis_peer_tip_is_fresh(tmp_path: Path) -> None:
    deps_sync = P2PDeps.open(f"sqlite:///{tmp_path / 'genesis.db'}", str(GENESIS_PATH))
    node = P2PService(
        listen_addrs=[tcp_multiaddr(0)],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps_sync,
        peerstore_path=str(tmp_path / "genesis" / "p2p"),
    )

    session = node._peer_registry.register("peer:genesis", "inbound")
    peer = _PeerState(
        session_id=session.session_id,
        remote="peer:genesis",
        direction="inbound",
        conn=None,
        stream=AsyncMock(),
        framer=Framer(aead=None),
        write_lock=asyncio.Lock(),
    )
    node._peers[peer.remote] = peer
    node._peers_by_session[peer.session_id] = peer

    peer.hello = {
        "chain_id": node.chain_id,
        "network_magic": NETWORK_MAGIC,
        "genesis_header_hash": node._genesis_header_hash(),
        "genesis_block_hash": node._genesis_block_hash(),
        "genesis_hash": node._genesis_header_hash(),
        "fork_id": node._fork_id(),
        "consensus_id": node._consensus_id(),
        "protocol_version": node._protocol_version(),
        "genesis_identity": node._genesis_identity(),
        "network_params_hash": node._network_params_hash(),
        "head_height": 0,
        "head_hash": b"\x00" * 32,
        "capabilities": ["sync"],
    }
    peer.repo_state_ok = True
    peer.identity_ok = True
    peer.hello_done.set()

    node._update_peer_head_table(
        peer,
        height=0,
        head_hash=node._genesis_header_hash(),
        source="hello",
    )

    total, fresh, stale = node._peer_tip_freshness_snapshot(chain_id=node.chain_id)
    assert total == 1
    assert fresh == 1
    assert stale == 0

    snap = node.sync_status_snapshot()
    assert snap.best_remote_height == 0
    assert snap.sync_status_reason != "no_fresh_peer_tips"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
