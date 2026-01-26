"""
Test that stale/unresponsive peer heights are excluded from network best height calculation.

This ensures that when a node at the highest height stops responding to block requests,
other nodes can reorganize to the active seed nodes instead of being stuck waiting for
the unresponsive peer.
"""

import asyncio
import time
from pathlib import Path

import pytest

from p2p.node.p2p_service import P2PService, _PeerHeadInfo, _PeerState
from p2p.tests import free_port, tcp_multiaddr
from p2p.tests.test_sync_loop_behavior import _make_deps


# Test configuration - aligned with P2PService defaults
STALE_THRESHOLD_SEC = 60.0  # Default value from P2PService._sync_peer_head_stale_sec
COOLDOWN_SEC = 120.0  # Default value from P2PService._sync_peer_head_cooldown_sec


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
    node._peers[peer_addr] = peer
    return peer


def test_stale_peer_height_excluded_from_network_best(tmp_path: Path) -> None:
    """Test that stale peer heights are not considered in network best height."""
    deps_sync, deps = _make_deps(tmp_path, "stale-peer-exclusion")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "stale-peer-exclusion" / "p2p"),
    )

    # Register two peers
    peer_stale = _register_peer(node, "peer:stale")
    peer_active = _register_peer(node, "peer:active")

    # Stale peer advertises high height but hasn't updated recently
    # Using 2x the stale threshold to ensure it's clearly stale
    now = time.time()
    node._sync_peer_heads[peer_stale.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now - (STALE_THRESHOLD_SEC * 2),  # Clearly stale
        source="test",
    )
    peer_stale.hello = {"head_height": 100}

    # Active peer has lower height but is fresh
    node._sync_peer_heads[peer_active.remote] = _PeerHeadInfo(
        height=50,
        updated_at=now,  # Fresh: just now
        source="test",
    )
    peer_active.hello = {"head_height": 50}

    # Network best height should be 50 (active peer), not 100 (stale peer)
    network_best = node._network_best_height()
    assert network_best == 50, f"Expected 50 from active peer, got {network_best}"


def test_cooldown_peer_height_excluded_from_network_best(tmp_path: Path) -> None:
    """Test that peers in cooldown are not considered in network best height."""
    deps_sync, deps = _make_deps(tmp_path, "cooldown-peer-exclusion")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "cooldown-peer-exclusion" / "p2p"),
    )

    # Register two peers
    peer_cooldown = _register_peer(node, "peer:cooldown")
    peer_active = _register_peer(node, "peer:active")

    # Peer in cooldown has high height but is penalized
    now = time.time()
    node._sync_peer_heads[peer_cooldown.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
        cooldown_until=now + COOLDOWN_SEC,  # In cooldown
    )
    peer_cooldown.hello = {"head_height": 100}

    # Active peer has lower height but no cooldown
    node._sync_peer_heads[peer_active.remote] = _PeerHeadInfo(
        height=50,
        updated_at=now,
        source="test",
        cooldown_until=0.0,  # No cooldown
    )
    peer_active.hello = {"head_height": 50}

    # Network best height should be 50 (active peer), not 100 (cooldown peer)
    network_best = node._network_best_height()
    assert network_best == 50, f"Expected 50 from active peer, got {network_best}"


def test_stale_peer_network_best_height_excluded(tmp_path: Path) -> None:
    """Test that network_best_height from stale peers is excluded."""
    deps_sync, deps = _make_deps(tmp_path, "stale-network-best-exclusion")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "stale-network-best-exclusion" / "p2p"),
    )

    # Register two peers
    peer_stale = _register_peer(node, "peer:stale")
    peer_active = _register_peer(node, "peer:active")

    # Stale peer reports very high network_best_height but is stale
    now = time.time()
    node._sync_peer_heads[peer_stale.remote] = _PeerHeadInfo(
        height=80,
        updated_at=now - (STALE_THRESHOLD_SEC * 2),  # Clearly stale
        source="test",
    )
    peer_stale.hello = {
        "head_height": 80,
        "network_best_height": 200,  # Claims network is at 200
    }

    # Active peer has moderate height
    node._sync_peer_heads[peer_active.remote] = _PeerHeadInfo(
        height=50,
        updated_at=now,  # Fresh: just now
        source="test",
    )
    peer_active.hello = {
        "head_height": 50,
        "network_best_height": 60,  # Claims network is at 60
    }

    # Network best should be 60 (from active peer), not 200 (from stale peer)
    network_best = node._network_best_height()
    assert network_best == 60, f"Expected 60, got {network_best}"


def test_cooldown_peer_network_best_height_excluded(tmp_path: Path) -> None:
    """Test that network_best_height from cooldown peers is excluded."""
    deps_sync, deps = _make_deps(tmp_path, "cooldown-network-best-exclusion")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "cooldown-network-best-exclusion" / "p2p"),
    )

    # Register two peers
    peer_cooldown = _register_peer(node, "peer:cooldown")
    peer_active = _register_peer(node, "peer:active")

    # Cooldown peer reports very high network_best_height but is in cooldown
    now = time.time()
    node._sync_peer_heads[peer_cooldown.remote] = _PeerHeadInfo(
        height=80,
        updated_at=now,
        source="test",
        cooldown_until=now + COOLDOWN_SEC,  # In cooldown
    )
    peer_cooldown.hello = {
        "head_height": 80,
        "network_best_height": 200,  # Claims network is at 200
    }

    # Active peer has moderate height
    node._sync_peer_heads[peer_active.remote] = _PeerHeadInfo(
        height=50,
        updated_at=now,
        source="test",
        cooldown_until=0.0,  # No cooldown
    )
    peer_active.hello = {
        "head_height": 50,
        "network_best_height": 60,  # Claims network is at 60
    }

    # Network best should be 60 (from active peer), not 200 (from cooldown peer)
    network_best = node._network_best_height()
    assert network_best == 60, f"Expected 60, got {network_best}"


def test_all_peers_stale_returns_none(tmp_path: Path) -> None:
    """Test that network_best_height returns None when all peers are stale."""
    deps_sync, deps = _make_deps(tmp_path, "all-stale")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "all-stale" / "p2p"),
    )

    # Register peers that are all stale
    peer1 = _register_peer(node, "peer:1")
    peer2 = _register_peer(node, "peer:2")

    now = time.time()
    node._sync_peer_heads[peer1.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now - (STALE_THRESHOLD_SEC * 3),  # Very stale
        source="test",
    )
    peer1.hello = {"head_height": 100}

    node._sync_peer_heads[peer2.remote] = _PeerHeadInfo(
        height=90,
        updated_at=now - (STALE_THRESHOLD_SEC * 2.5),  # Also stale
        source="test",
    )
    peer2.hello = {"head_height": 90}

    # All peers are stale, should return None
    network_best = node._network_best_height()
    assert network_best is None, f"Expected None when all peers stale, got {network_best}"


def test_peer_becomes_responsive_again(tmp_path: Path) -> None:
    """Test that a peer's height is included again once it becomes responsive."""
    deps_sync, deps = _make_deps(tmp_path, "peer-recovery")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "peer-recovery" / "p2p"),
    )

    peer = _register_peer(node, "peer:test")

    # Initially, peer is stale
    now = time.time()
    node._sync_peer_heads[peer.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now - (STALE_THRESHOLD_SEC * 2),  # Stale
        source="test",
    )
    peer.hello = {"head_height": 100}

    # Should return None (no active peers)
    assert node._network_best_height() is None

    # Update the peer to be responsive again
    node._sync_peer_heads[peer.remote].updated_at = now

    # Now it should return the peer's height
    network_best = node._network_best_height()
    assert network_best == 100, f"Expected 100 after peer recovery, got {network_best}"
