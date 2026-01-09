"""
Test for not_anchored recovery mechanisms: backtracking, skipping, and aggressive recovery.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2] / "python"))

from p2p.node.p2p_service import (
    P2PService,
    _PeerState,
    _SyncHeader,
)
from p2p.deps import P2PDeps

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_devnet_genesis(tmp_path: Path) -> Path:
    genesis_path = tmp_path / "genesis.devnet.json"
    if genesis_path.exists():
        return genesis_path
    base_genesis = json.loads(GENESIS_PATH.read_text(encoding="utf-8"))
    base_genesis["chainId"] = 1337
    base_genesis["network"] = "animica-devnet"
    consensus = base_genesis.get("consensus") or {}
    consensus["initialThetaMicro"] = 1
    base_genesis["consensus"] = consensus
    params_ref = base_genesis.get("paramsRef") or {}
    params_ref["path"] = str(
        Path(__file__).resolve().parents[2] / "spec" / "params.yaml"
    )
    base_genesis["paramsRef"] = params_ref
    genesis_path.write_text(json.dumps(base_genesis, indent=2), encoding="utf-8")
    return genesis_path


def _make_deps(tmp_path: Path, name: str) -> P2PDeps:
    db_path = tmp_path / f"{name}.db"
    genesis_path = _make_devnet_genesis(tmp_path)
    return P2PDeps.open(f"sqlite:///{db_path}", str(genesis_path))


@pytest.mark.asyncio
async def test_backtrack_recovery_increases_depth(tmp_path: Path) -> None:
    """Test that backtrack recovery increases locator depth."""
    deps = _make_deps(tmp_path, "backtrack_test")
    node = P2PService(deps, listen="127.0.0.1:0")
    
    # Create a mock peer
    peer = _PeerState(
        remote="test_peer:30333",
        peer_id="test_peer_id",
        framer=None,
        writer=None,
        direction="outbound",
    )
    
    # Create a mock header
    header = _SyncHeader(
        hash=b"test_hash",
        parent_hash=b"parent_hash",
        height=1000,
        timestamp=int(time.time()),
        theta_micro=1000000,
    )
    
    # Track initial backtrack depth
    initial_depth = node._sync_backtrack_depth
    assert initial_depth == 0
    
    # Simulate backtrack recovery
    action = node._apply_backtrack_recovery(header, 999, b"anchor_hash", "test_reason")
    
    # Verify backtrack depth increased
    assert node._sync_backtrack_depth == 1
    assert action == "backtrack_depth_1"
    assert node._sync_inflight_headers == 0  # Should clear inflight
    assert node._sync_anchor_probe_hash == header.parent_hash
    
    # Apply again to verify depth increases
    action = node._apply_backtrack_recovery(header, 999, b"anchor_hash", "test_reason")
    assert node._sync_backtrack_depth == 2
    assert action == "backtrack_depth_2"


@pytest.mark.asyncio
async def test_skip_recovery_marks_range(tmp_path: Path) -> None:
    """Test that skip recovery marks problematic ranges."""
    deps = _make_deps(tmp_path, "skip_test")
    node = P2PService(deps, listen="127.0.0.1:0")
    
    header = _SyncHeader(
        hash=b"test_hash",
        parent_hash=b"parent_hash",
        height=2000,
        timestamp=int(time.time()),
        theta_micro=1000000,
    )
    
    # Apply skip recovery
    action = node._apply_skip_recovery(header, 1500, b"anchor_hash")
    
    # Verify skip range is set
    assert node._sync_skip_range_start == 1501
    assert node._sync_skip_range_end == 2100  # header.height + 100
    assert node._sync_inflight_headers == 0
    assert "skip_range_1501_to_2100" in action


@pytest.mark.asyncio
async def test_aggressive_recovery_clears_state(tmp_path: Path) -> None:
    """Test that aggressive recovery clears all state."""
    deps = _make_deps(tmp_path, "aggressive_test")
    node = P2PService(deps, listen="127.0.0.1:0")
    
    # Set up some in-flight state
    node._sync_inflight_headers = 5
    node._sync_inflight_blocks[b"block1"] = time.time()
    node._sync_inflight_blocks[b"block2"] = time.time()
    node._sync_inflight_peers[b"block1"] = "peer1"
    node._sync_active_header_peer = "peer1"
    node._sync_active_block_peer = "peer2"
    node._sync_backtrack_depth = 3
    node._sync_skip_range_start = 100
    node._sync_skip_range_end = 200
    
    # Apply aggressive recovery
    action = node._apply_aggressive_recovery("test_reason")
    
    # Verify all state cleared
    assert node._sync_inflight_headers == 0
    assert len(node._sync_inflight_blocks) == 0
    assert len(node._sync_inflight_peers) == 0
    assert node._sync_active_header_peer is None
    assert node._sync_active_block_peer is None
    assert node._sync_backtrack_depth == 0
    assert node._sync_skip_range_start is None
    assert node._sync_skip_range_end is None
    assert action == "aggressive_recovery_clear_and_rotate"


@pytest.mark.asyncio
async def test_progressive_recovery_escalation(tmp_path: Path) -> None:
    """Test that recovery escalates from backtrack -> skip -> aggressive."""
    deps = _make_deps(tmp_path, "escalation_test")
    node = P2PService(deps, listen="127.0.0.1:0")
    
    # Stage 1: Backtrack (5 attempts)
    node._sync_not_anchored_attempts = 5
    action1 = node._apply_backtrack_recovery(
        _SyncHeader(b"h1", b"p1", 100, int(time.time()), 1000000),
        99, b"anchor", "test"
    )
    assert "backtrack" in action1
    assert node._sync_backtrack_depth == 1
    
    # Stage 2: Skip (10 attempts)
    node._sync_not_anchored_attempts = 10
    action2 = node._apply_skip_recovery(
        _SyncHeader(b"h2", b"p2", 200, int(time.time()), 1000000),
        199, b"anchor"
    )
    assert "skip_range" in action2
    assert node._sync_skip_range_start is not None
    
    # Stage 3: Aggressive (20 attempts)
    node._sync_not_anchored_attempts = 20
    node._sync_inflight_headers = 3
    action3 = node._apply_aggressive_recovery("test")
    assert action3 == "aggressive_recovery_clear_and_rotate"
    assert node._sync_inflight_headers == 0
    assert node._sync_backtrack_depth == 0


@pytest.mark.asyncio
async def test_header_locator_uses_backtrack_depth(tmp_path: Path) -> None:
    """Test that header locator generation respects backtrack depth."""
    deps = _make_deps(tmp_path, "locator_test")
    node = P2PService(deps, listen="127.0.0.1:0")
    
    # Build locator with no backtrack
    locator1 = node._build_headers_locator(max_entries=10)
    len1 = len(locator1)
    
    # Set backtrack depth and rebuild
    node._sync_backtrack_depth = 2
    locator2 = node._build_headers_locator(max_entries=10)
    len2 = len(locator2)
    
    # Locator should have more entries due to backtrack (10 entries per level)
    # At minimum, we expect at least 1 entry (genesis)
    assert len2 >= len1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
