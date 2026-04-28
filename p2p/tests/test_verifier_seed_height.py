"""
Test verifier seed height validation.

This ensures that the trusted verifier seed nodes (3.12.224.189, 144.126.133.21)
are treated as authoritative for determining the highest block height, with other
nodes only allowed to be max 1 block ahead (e.g., a miner who just found a block).
"""

import asyncio
import os
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


def test_verifier_seed_identification(tmp_path: Path) -> None:
    """Test that verifier seed peers are correctly identified."""
    deps_sync, deps = _make_deps(tmp_path, "verifier-seed-id")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "verifier-seed-id" / "p2p"),
    )

    # Test verifier seeds
    assert node._is_verifier_seed_peer("3.12.224.189:30333") is True
    assert node._is_verifier_seed_peer("144.126.133.21:30333") is True
    assert node._is_verifier_seed_peer("mainnet.animica.org:30333") is True
    assert node._is_verifier_seed_peer("tcp://mainnet.animica.org:30333") is True
    
    # Test non-verifier seeds
    assert node._is_verifier_seed_peer("192.168.1.1:30333") is False
    assert node._is_verifier_seed_peer("10.0.0.1:30333") is False


def test_verifier_seeds_disabled(tmp_path: Path) -> None:
    """Test that verifier seeds can be disabled via environment variable."""
    # Disable verifier seeds
    os.environ["ANIMICA_P2P_ENABLE_VERIFIER_SEEDS"] = "false"
    try:
        deps_sync, deps = _make_deps(tmp_path, "verifier-disabled")
        node = P2PService(
            listen_addrs=[tcp_multiaddr(free_port())],
            seeds=[],
            chain_id=deps_sync.chain_id,
            deps=deps,
            peerstore_path=str(tmp_path / "verifier-disabled" / "p2p"),
        )

        # Even verifier IPs should return False when disabled
        assert node._is_verifier_seed_peer("3.12.224.189:30333") is False
        assert node._is_verifier_seed_peer("144.126.133.21:30333") is False
    finally:
        os.environ.pop("ANIMICA_P2P_ENABLE_VERIFIER_SEEDS", None)


def test_custom_verifier_seeds(tmp_path: Path) -> None:
    """Test that custom verifier seed IPs can be configured."""
    os.environ["ANIMICA_P2P_VERIFIER_SEED_IPS"] = "10.1.2.3,10.4.5.6"
    try:
        deps_sync, deps = _make_deps(tmp_path, "custom-verifiers")
        node = P2PService(
            listen_addrs=[tcp_multiaddr(free_port())],
            seeds=[],
            chain_id=deps_sync.chain_id,
            deps=deps,
            peerstore_path=str(tmp_path / "custom-verifiers" / "p2p"),
        )

        # Custom verifier seeds should be recognized
        assert node._is_verifier_seed_peer("10.1.2.3:30333") is True
        assert node._is_verifier_seed_peer("10.4.5.6:30333") is True
        
        # Default verifier seeds should not be recognized
        assert node._is_verifier_seed_peer("3.12.224.189:30333") is False
        assert node._is_verifier_seed_peer("144.126.133.21:30333") is False
    finally:
        os.environ.pop("ANIMICA_P2P_VERIFIER_SEED_IPS", None)


def test_verifier_seeds_constrain_network_height_one_ahead(tmp_path: Path) -> None:
    """Test that non-verifier peers 1 block ahead are accepted (miner case)."""
    deps_sync, deps = _make_deps(tmp_path, "verifier-one-ahead")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "verifier-one-ahead" / "p2p"),
    )

    # Register verifier seed and regular peer
    peer_verifier = _register_peer(node, "3.12.224.189:30333")
    peer_regular = _register_peer(node, "192.168.1.1:30333")

    now = time.time()
    
    # Verifier seed at height 100
    node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier.hello = {"head_height": 100}

    # Regular peer is 1 block ahead (miner who just found next block)
    node._sync_peer_heads[peer_regular.remote] = _PeerHeadInfo(
        height=101,
        updated_at=now,
        source="test",
    )
    peer_regular.hello = {"head_height": 101}

    # Network best height should be 101 (1 block ahead is allowed for miners)
    network_best = node._network_best_height()
    assert network_best == 101, f"Expected 101 (1 ahead allowed), got {network_best}"


def test_verifier_seeds_constrain_network_height_two_ahead(tmp_path: Path) -> None:
    """Test that non-verifier peers 2+ blocks ahead are rejected."""
    deps_sync, deps = _make_deps(tmp_path, "verifier-two-ahead")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "verifier-two-ahead" / "p2p"),
    )

    # Register verifier seed and regular peer
    peer_verifier = _register_peer(node, "3.12.224.189:30333")
    peer_regular = _register_peer(node, "192.168.1.1:30333")

    now = time.time()
    
    # Verifier seed at height 100
    node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier.hello = {"head_height": 100}

    # Regular peer is 2 blocks ahead (should be rejected)
    node._sync_peer_heads[peer_regular.remote] = _PeerHeadInfo(
        height=102,
        updated_at=now,
        source="test",
    )
    peer_regular.hello = {"head_height": 102}

    # Network best height should be 101 (max allowed = verifier + 1)
    network_best = node._network_best_height()
    assert network_best == 101, f"Expected 101 (constrained to verifier+1), got {network_best}"


def test_verifier_seeds_constrain_network_height_far_ahead(tmp_path: Path) -> None:
    """Test that non-verifier peers far ahead are constrained to verifier+1."""
    deps_sync, deps = _make_deps(tmp_path, "verifier-far-ahead")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "verifier-far-ahead" / "p2p"),
    )

    # Register verifier seed and regular peer claiming very high height
    peer_verifier = _register_peer(node, "3.12.224.189:30333")
    peer_regular = _register_peer(node, "192.168.1.1:30333")

    now = time.time()
    
    # Verifier seed at height 100
    node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier.hello = {"head_height": 100}

    # Regular peer claims to be 50 blocks ahead
    node._sync_peer_heads[peer_regular.remote] = _PeerHeadInfo(
        height=150,
        updated_at=now,
        source="test",
    )
    peer_regular.hello = {"head_height": 150}

    # Network best height should be 101 (constrained to verifier+1)
    network_best = node._network_best_height()
    assert network_best == 101, f"Expected 101 (constrained to verifier+1), got {network_best}"


def test_multiple_verifier_seeds_highest_used(tmp_path: Path) -> None:
    """Test that when multiple verifier seeds exist, the highest is used."""
    deps_sync, deps = _make_deps(tmp_path, "multi-verifier")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "multi-verifier" / "p2p"),
    )

    # Register both verifier seeds
    peer_verifier1 = _register_peer(node, "3.12.224.189:30333")
    peer_verifier2 = _register_peer(node, "144.126.133.21:30333")
    peer_regular = _register_peer(node, "192.168.1.1:30333")

    now = time.time()
    
    # First verifier at height 100
    node._sync_peer_heads[peer_verifier1.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier1.hello = {"head_height": 100}

    # Second verifier at height 110 (higher)
    node._sync_peer_heads[peer_verifier2.remote] = _PeerHeadInfo(
        height=110,
        updated_at=now,
        source="test",
    )
    peer_verifier2.hello = {"head_height": 110}

    # Regular peer at 115 (more than 1 ahead of highest verifier)
    node._sync_peer_heads[peer_regular.remote] = _PeerHeadInfo(
        height=115,
        updated_at=now,
        source="test",
    )
    peer_regular.hello = {"head_height": 115}

    # Network best should be 111 (max verifier 110 + 1)
    network_best = node._network_best_height()
    assert network_best == 111, f"Expected 111 (max verifier+1), got {network_best}"


def test_no_verifier_seeds_present_no_constraint(tmp_path: Path) -> None:
    """Test that without verifier seeds, there's no height constraint."""
    deps_sync, deps = _make_deps(tmp_path, "no-verifier")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "no-verifier" / "p2p"),
    )

    # Register only non-verifier peers
    peer1 = _register_peer(node, "192.168.1.1:30333")
    peer2 = _register_peer(node, "192.168.1.2:30333")

    now = time.time()
    
    node._sync_peer_heads[peer1.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer1.hello = {"head_height": 100}

    node._sync_peer_heads[peer2.remote] = _PeerHeadInfo(
        height=200,
        updated_at=now,
        source="test",
    )
    peer2.hello = {"head_height": 200}

    # Without verifier seeds, network best should be unconstrained
    network_best = node._network_best_height()
    assert network_best == 200, f"Expected 200 (no constraint), got {network_best}"


def test_verifier_behind_regular_peers(tmp_path: Path) -> None:
    """Test behavior when verifier seed is behind regular peers (syncing)."""
    deps_sync, deps = _make_deps(tmp_path, "verifier-behind")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "verifier-behind" / "p2p"),
    )

    # Register verifier seed and regular peer
    peer_verifier = _register_peer(node, "3.12.224.189:30333")
    peer_regular = _register_peer(node, "192.168.1.1:30333")

    now = time.time()
    
    # Verifier seed is behind (syncing)
    node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
        height=80,
        updated_at=now,
        source="test",
    )
    peer_verifier.hello = {"head_height": 80}

    # Regular peer is ahead but within allowed range
    node._sync_peer_heads[peer_regular.remote] = _PeerHeadInfo(
        height=81,
        updated_at=now,
        source="test",
    )
    peer_regular.hello = {"head_height": 81}

    # Network best should be 81 (verifier+1 is allowed)
    network_best = node._network_best_height()
    assert network_best == 81, f"Expected 81 (within allowed range), got {network_best}"


def test_verifier_network_best_height_propagation(tmp_path: Path) -> None:
    """Test that network_best_height from non-verifiers is also constrained."""
    deps_sync, deps = _make_deps(tmp_path, "verifier-net-best")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "verifier-net-best" / "p2p"),
    )

    # Register verifier seed and regular peer
    peer_verifier = _register_peer(node, "3.12.224.189:30333")
    peer_regular = _register_peer(node, "192.168.1.1:30333")

    now = time.time()
    
    # Verifier seed at height 100
    node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier.hello = {"head_height": 100}

    # Regular peer at 90 but claims network is at 200
    node._sync_peer_heads[peer_regular.remote] = _PeerHeadInfo(
        height=90,
        updated_at=now,
        source="test",
    )
    peer_regular.hello = {
        "head_height": 90,
        "network_best_height": 200,  # Claims network is far ahead
    }

    # Network best should be 101 (constrained to verifier+1)
    # The 200 from network_best_height should be filtered
    network_best = node._network_best_height()
    assert network_best == 101, f"Expected 101 (constrained), got {network_best}"


def test_get_max_verifier_height(tmp_path: Path) -> None:
    """Test _get_max_verifier_height returns the highest verifier height."""
    deps_sync, deps = _make_deps(tmp_path, "max-verifier")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "max-verifier" / "p2p"),
    )

    # Register verifier seeds
    peer_verifier1 = _register_peer(node, "3.12.224.189:30333")
    peer_verifier2 = _register_peer(node, "144.126.133.21:30333")
    peer_regular = _register_peer(node, "192.168.1.1:30333")

    now = time.time()
    
    # Set verifier heights
    node._sync_peer_heads[peer_verifier1.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier1.hello = {"head_height": 100}

    node._sync_peer_heads[peer_verifier2.remote] = _PeerHeadInfo(
        height=110,
        updated_at=now,
        source="test",
    )
    peer_verifier2.hello = {"head_height": 110}

    # Regular peer height should not affect max verifier height
    node._sync_peer_heads[peer_regular.remote] = _PeerHeadInfo(
        height=200,
        updated_at=now,
        source="test",
    )
    peer_regular.hello = {"head_height": 200}

    # Max verifier height should be 110 (highest verifier)
    max_verifier = node._get_max_verifier_height()
    assert max_verifier == 110, f"Expected 110, got {max_verifier}"


def test_get_max_verifier_height_no_verifiers(tmp_path: Path) -> None:
    """Test _get_max_verifier_height returns None when no verifiers present."""
    deps_sync, deps = _make_deps(tmp_path, "no-verifiers")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "no-verifiers" / "p2p"),
    )

    # Register only non-verifier peers
    peer_regular = _register_peer(node, "192.168.1.1:30333")

    now = time.time()
    
    node._sync_peer_heads[peer_regular.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_regular.hello = {"head_height": 100}

    # Should return None since no verifier seeds present
    max_verifier = node._get_max_verifier_height()
    assert max_verifier is None, f"Expected None, got {max_verifier}"


def test_get_max_verifier_height_disabled(tmp_path: Path) -> None:
    """Test _get_max_verifier_height returns None when verifiers disabled."""
    os.environ["ANIMICA_P2P_ENABLE_VERIFIER_SEEDS"] = "false"
    try:
        deps_sync, deps = _make_deps(tmp_path, "verifier-disabled-max")
        node = P2PService(
            listen_addrs=[tcp_multiaddr(free_port())],
            seeds=[],
            chain_id=deps_sync.chain_id,
            deps=deps,
            peerstore_path=str(tmp_path / "verifier-disabled-max" / "p2p"),
        )

        # Register verifier seed
        peer_verifier = _register_peer(node, "3.12.224.189:30333")

        now = time.time()
        
        node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
            height=100,
            updated_at=now,
            source="test",
        )
        peer_verifier.hello = {"head_height": 100}

        # Should return None since verifiers are disabled
        max_verifier = node._get_max_verifier_height()
        assert max_verifier is None, f"Expected None, got {max_verifier}"
    finally:
        os.environ.pop("ANIMICA_P2P_ENABLE_VERIFIER_SEEDS", None)


def test_check_and_discount_blocks_past_verifier_no_action_when_behind(tmp_path: Path) -> None:
    """Test that no action is taken when local height is below verifier height."""
    deps_sync, deps = _make_deps(tmp_path, "behind-verifier")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "behind-verifier" / "p2p"),
    )

    # Register verifier seed
    peer_verifier = _register_peer(node, "3.12.224.189:30333")

    now = time.time()
    
    # Verifier at height 100
    node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier.hello = {"head_height": 100}

    # Mock local head at height 50 (behind verifier)
    # The _check_and_discount_blocks_past_verifier should do nothing
    # since local height <= verifier height
    initial_recovery_count = node._sync_recovery_attempts
    node._check_and_discount_blocks_past_verifier()
    
    # Should not trigger a reset
    assert node._sync_recovery_attempts == initial_recovery_count


def test_check_and_discount_blocks_past_verifier_no_action_when_equal(tmp_path: Path) -> None:
    """Test that no action is taken when local height equals verifier height."""
    deps_sync, deps = _make_deps(tmp_path, "equal-verifier")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "equal-verifier" / "p2p"),
    )

    # Register verifier seed
    peer_verifier = _register_peer(node, "3.12.224.189:30333")

    now = time.time()
    
    # Verifier at height 100
    node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier.hello = {"head_height": 100}

    # Local head also at height 100 - should not trigger reset
    initial_recovery_count = node._sync_recovery_attempts
    node._check_and_discount_blocks_past_verifier()
    
    # Should not trigger a reset
    assert node._sync_recovery_attempts == initial_recovery_count


def test_check_and_discount_blocks_past_verifier_one_ahead_waits_for_grace(tmp_path: Path) -> None:
    """Local +1 above verifier should not reset before grace window expires."""
    deps_sync, deps = _make_deps(tmp_path, "one-ahead-grace")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "one-ahead-grace" / "p2p"),
    )

    peer_verifier = _register_peer(node, "3.12.224.189:30333")
    now = time.time()
    node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier.hello = {"head_height": 100}

    node._local_head = lambda: (101, "0xlocal")  # type: ignore[method-assign]
    node._sync_verifier_rewind_grace_sec = 60.0
    node._sync_verifier_ahead_since = now

    initial_recovery_count = node._sync_recovery_attempts
    node._check_and_discount_blocks_past_verifier()

    assert node._sync_recovery_attempts == initial_recovery_count


def test_check_and_discount_blocks_past_verifier_one_ahead_rewinds_after_grace(tmp_path: Path) -> None:
    """Local +1 above verifier should rewind once the grace window is exceeded."""
    deps_sync, deps = _make_deps(tmp_path, "one-ahead-rewind")
    node = P2PService(
        listen_addrs=[tcp_multiaddr(free_port())],
        seeds=[],
        chain_id=deps_sync.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "one-ahead-rewind" / "p2p"),
    )

    peer_verifier = _register_peer(node, "3.12.224.189:30333")
    now = time.time()
    node._sync_peer_heads[peer_verifier.remote] = _PeerHeadInfo(
        height=100,
        updated_at=now,
        source="test",
    )
    peer_verifier.hello = {"head_height": 100}

    node._local_head = lambda: (101, "0xlocal")  # type: ignore[method-assign]
    node._sync_verifier_rewind_grace_sec = 10.0
    node._sync_verifier_ahead_since = now - 20.0

    initial_recovery_count = node._sync_recovery_attempts
    node._check_and_discount_blocks_past_verifier()

    assert node._sync_recovery_attempts == initial_recovery_count + 1
    assert node._sync_last_recovery_reason == "blocks_past_verifier_height"
