"""
Test verifier seed status method.

Tests the get_verifier_seed_status() method which provides mining eligibility
information based on verifier seed heights.
"""

import asyncio
import time
from pathlib import Path

import pytest

from p2p.node.p2p_service import P2PService, _PeerHeadInfo, _PeerState
from p2p.tests import free_port, tcp_multiaddr
from p2p.tests.test_sync_loop_behavior import _make_deps


def _register_peer(node, peer_addr: str):
    """Helper to register a peer for testing."""
    session = node._peer_registry.register(peer_addr, "outbound")
    peer = _PeerState(
        session_id=session.session_id,
        remote=peer_addr,
        direction="outbound",
        conn=None,
        stream=None,
        framer=None,
        write_lock=asyncio.Lock(),
    )
    peer.hello_done = asyncio.Event()
    peer.hello_done.set()
    peer.repo_state_ok = True
    peer.peer_id = peer_addr.replace(":", "_")
    node._peers[(peer_addr, "outbound")] = peer
    return peer


def test_get_verifier_seed_status(tmp_path: Path) -> None:
    """Test the get_verifier_seed_status method returns correct information."""
    deps_sync, deps = _make_deps(tmp_path, "get-verifier-status")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "get-verifier-status" / "p2p"),
    )
    
    # Register verifier seed peers
    verifier1 = _register_peer(node, "3.12.224.189:30333")
    verifier2 = _register_peer(node, "144.126.133.21:30333")
    
    # Register non-verifier peer
    regular = _register_peer(node, "192.168.1.1:30333")
    
    now = time.time()
    
    # Set heights
    node._sync_peer_heads["3.12.224.189:30333"] = _PeerHeadInfo(
        height=100, updated_at=now, source="test"
    )
    node._sync_peer_heads["144.126.133.21:30333"] = _PeerHeadInfo(
        height=105, updated_at=now, source="test"
    )
    node._sync_peer_heads["192.168.1.1:30333"] = _PeerHeadInfo(
        height=110, updated_at=now, source="test"
    )
    
    # Mock local head
    node._local_head = lambda: (103, "0x" + "00" * 32)
    
    # Get status
    status = node.get_verifier_seed_status()
    
    # Validate response structure
    assert isinstance(status, dict)
    assert status["enabled"] is True
    assert set(status["configured_ips"]) == {"3.12.224.189", "144.126.133.21"}
    assert len(status["connected_verifiers"]) == 2
    assert status["max_verifier_height"] == 105  # Highest verifier
    assert status["max_allowed_height"] == 106  # Verifier + 1
    assert status["local_height"] == 103
    assert status["can_mine"] is True  # 103 <= 106
    
    # Verify connected verifiers details
    verifier_remotes = [v["remote"] for v in status["connected_verifiers"]]
    assert "3.12.224.189:30333" in verifier_remotes
    assert "144.126.133.21:30333" in verifier_remotes
    assert "192.168.1.1:30333" not in verifier_remotes  # Not a verifier


def test_get_verifier_seed_status_cannot_mine(tmp_path: Path) -> None:
    """Test that get_verifier_seed_status correctly identifies when mining is not allowed."""
    deps_sync, deps = _make_deps(tmp_path, "verifier-cannot-mine")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "verifier-cannot-mine" / "p2p"),
    )
    
    # Register verifier seed peer
    verifier1 = _register_peer(node, "3.12.224.189:30333")
    
    now = time.time()
    
    # Set verifier at height 100
    node._sync_peer_heads["3.12.224.189:30333"] = _PeerHeadInfo(
        height=100, updated_at=now, source="test"
    )
    
    # Mock local head at 103 (2 blocks ahead - not allowed)
    node._local_head = lambda: (103, "0x" + "00" * 32)
    
    # Get status
    status = node.get_verifier_seed_status()
    
    # Validate
    assert status["max_verifier_height"] == 100
    assert status["max_allowed_height"] == 101
    assert status["local_height"] == 103
    assert status["can_mine"] is False  # 103 > 101, not allowed


def test_get_verifier_seed_status_at_boundary(tmp_path: Path) -> None:
    """Test mining allowed at verifier_height + 1 (boundary case)."""
    deps_sync, deps = _make_deps(tmp_path, "verifier-boundary")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "verifier-boundary" / "p2p"),
    )
    
    # Register verifier seed peer
    verifier1 = _register_peer(node, "3.12.224.189:30333")
    
    now = time.time()
    
    # Set verifier at height 100
    node._sync_peer_heads["3.12.224.189:30333"] = _PeerHeadInfo(
        height=100, updated_at=now, source="test"
    )
    
    # Mock local head at 101 (exactly at boundary)
    node._local_head = lambda: (101, "0x" + "00" * 32)
    
    # Get status
    status = node.get_verifier_seed_status()
    
    # Validate - should be allowed
    assert status["max_verifier_height"] == 100
    assert status["max_allowed_height"] == 101
    assert status["local_height"] == 101
    assert status["can_mine"] is True  # 101 == 101, allowed


def test_get_verifier_seed_status_no_verifiers(tmp_path: Path) -> None:
    """Test status when no verifier seeds are connected."""
    deps_sync, deps = _make_deps(tmp_path, "no-verifiers")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "no-verifiers" / "p2p"),
    )
    
    # Register only regular peer (no verifiers)
    regular = _register_peer(node, "192.168.1.1:30333")
    
    now = time.time()
    node._sync_peer_heads["192.168.1.1:30333"] = _PeerHeadInfo(
        height=100, updated_at=now, source="test"
    )
    
    # Mock local head
    node._local_head = lambda: (95, "0x" + "00" * 32)
    
    # Get status
    status = node.get_verifier_seed_status()
    
    # Validate - no verifiers means can mine (backward compatible)
    assert status["enabled"] is True
    assert len(status["connected_verifiers"]) == 0
    assert status["max_verifier_height"] is None
    assert status["max_allowed_height"] is None
    assert status["can_mine"] is True  # No verifiers connected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
