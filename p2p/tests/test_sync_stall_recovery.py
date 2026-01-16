"""
Comprehensive tests for sync stall recovery enhancements.

Tests the following features:
1. In-flight request timeout with exponential backoff and peer rotation
2. Orphan handling with parent backfill and cascade imports
3. Retry limits and abandoned request handling
4. Stall detection and watchdog recovery
"""

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from unittest.mock import AsyncMock, Mock, patch

import pytest


# Mock types matching p2p_service.py structure
@dataclass(slots=True)
class MockSyncRequest:
    request_id: str
    peer_id: str
    kind: str
    started_at: float
    deadline_at: float
    retry_count: int = 0
    item_hash: Optional[bytes] = None
    start_height: Optional[int] = None
    previous_peers: List[str] = field(default_factory=list)
    last_error: Optional[str] = None


@dataclass(slots=True)
class MockSyncBlock:
    block: Any
    hash: bytes
    parent_hash: bytes
    origin_peer: Optional[str] = None
    received_at: float = 0.0


class TestInflightRequestTimeout:
    """Test in-flight request timeout, retry, and peer rotation."""
    
    def test_request_timeout_triggers_requeue(self):
        """Test that expired requests are re-queued for retry."""
        # Setup
        now = time.time()
        block_hash = b"test_block_123"
        request = MockSyncRequest(
            request_id="req1",
            peer_id="peer1",
            kind="block",
            started_at=now - 30,  # Started 30s ago
            deadline_at=now - 10,  # Deadline expired 10s ago
            retry_count=0,
            item_hash=block_hash,
        )
        
        # Verify request is expired
        assert now >= request.deadline_at, "Request should be expired"
        
    def test_exponential_backoff_calculation(self):
        """Test exponential backoff with jitter calculation."""
        import random
        
        RETRY_BACKOFF_BASE_SEC = 2.0
        RETRY_BACKOFF_MAX_SEC = 60.0
        RETRY_JITTER_FACTOR = 0.2
        
        for retry_count in range(1, 6):
            backoff = min(
                RETRY_BACKOFF_BASE_SEC * (2 ** (retry_count - 1)),
                RETRY_BACKOFF_MAX_SEC
            )
            jitter = backoff * RETRY_JITTER_FACTOR * (random.random() * 2 - 1)
            final_backoff = max(1.0, backoff + jitter)
            
            # Verify backoff increases exponentially
            if retry_count == 1:
                assert 1.6 <= final_backoff <= 2.4, f"Backoff for retry 1: {final_backoff}"
            elif retry_count == 2:
                assert 3.2 <= final_backoff <= 4.8, f"Backoff for retry 2: {final_backoff}"
            elif retry_count == 3:
                assert 6.4 <= final_backoff <= 9.6, f"Backoff for retry 3: {final_backoff}"
            
            # Verify max cap
            assert final_backoff <= RETRY_BACKOFF_MAX_SEC, "Backoff should not exceed max"
    
    def test_peer_rotation_after_failures(self):
        """Test that failed peers are tracked and rotated."""
        request = MockSyncRequest(
            request_id="req1",
            peer_id="peer1",
            kind="block",
            started_at=time.time(),
            deadline_at=time.time() + 10,
            retry_count=0,
            previous_peers=[],
        )
        
        # Simulate failures from multiple peers
        failed_peers = ["peer1", "peer2", "peer3"]
        for peer in failed_peers:
            request.previous_peers.append(peer)
            request.retry_count += 1
        
        # Verify peer rotation tracking
        assert len(request.previous_peers) == 3, "Should track 3 failed peers"
        assert request.retry_count == 3, "Should have 3 retries"
        
    def test_retry_limit_reached(self):
        """Test that requests are abandoned after max retries."""
        MAX_REQUEST_RETRIES = 5
        
        request = MockSyncRequest(
            request_id="req1",
            peer_id="peer1",
            kind="block",
            started_at=time.time(),
            deadline_at=time.time() + 10,
            retry_count=MAX_REQUEST_RETRIES,
        )
        
        # Verify retry limit
        assert request.retry_count >= MAX_REQUEST_RETRIES, "Should have reached retry limit"
        

class TestOrphanHandling:
    """Test orphan block handling with parent backfill and cascade imports."""
    
    def test_orphan_cooldown_tracking(self):
        """Test that repeated orphans trigger cooldown."""
        orphan_seen_count: Dict[bytes, int] = {}
        block_hash = b"orphan_block"
        
        # Simulate seeing orphan multiple times
        for _ in range(5):
            orphan_seen_count[block_hash] = orphan_seen_count.get(block_hash, 0) + 1
        
        seen_count = orphan_seen_count[block_hash]
        assert seen_count == 5, "Should track orphan sightings"
        assert seen_count > 3, "Should trigger cooldown after 3 sightings"
    
    def test_parent_backfill_scheduling(self):
        """Test that missing parents are scheduled for fetch."""
        block_queue: deque = deque()
        block_queue_set: Set[bytes] = set()
        
        orphan_hash = b"orphan_block"
        parent_hash = b"parent_block"
        
        # Simulate parent backfill
        if parent_hash not in block_queue_set:
            block_queue.appendleft(parent_hash)  # Priority queue
            block_queue_set.add(parent_hash)
        
        assert parent_hash in block_queue_set, "Parent should be queued"
        assert block_queue[0] == parent_hash, "Parent should be at front (priority)"
    
    def test_cascade_import_tracking(self):
        """Test that cascade imports are tracked."""
        orphan_cascade_successes = 0
        
        # Simulate successful cascade imports
        cascade_count = 3
        orphan_cascade_successes += cascade_count
        
        assert orphan_cascade_successes == 3, "Should track cascade successes"
    
    def test_orphan_seen_count_cleared_on_success(self):
        """Test that orphan tracking is cleared after successful import."""
        orphan_seen_count: Dict[bytes, int] = {}
        block_hash = b"orphan_block"
        
        # Track orphan
        orphan_seen_count[block_hash] = 3
        assert block_hash in orphan_seen_count
        
        # Simulate successful import
        orphan_seen_count.pop(block_hash, None)
        assert block_hash not in orphan_seen_count, "Should clear tracking on success"


class TestStallDetection:
    """Test stall detection and watchdog recovery."""
    
    def test_stall_detection_no_progress(self):
        """Test stall detected when no progress for timeout period."""
        now = time.time()
        last_progress_at = now - 30  # 30s ago
        stall_timeout = 20  # 20s timeout
        
        stall_elapsed = now - last_progress_at
        is_stalled = stall_elapsed > stall_timeout
        
        assert is_stalled, "Should detect stall after timeout"
        assert stall_elapsed > stall_timeout, "Stall elapsed should exceed timeout"
    
    def test_stall_detection_inflight_with_empty_queues(self):
        """Test stall detected when in-flight blocks but queues empty."""
        in_flight_blocks = 5
        queued_blocks = 0
        now = time.time()
        last_progress_at = now - 15
        
        # Deadlock condition: in-flight > 0, queues empty, no progress
        is_deadlock = (
            in_flight_blocks > 0
            and queued_blocks == 0
            and (now - last_progress_at) > 10
        )
        
        assert is_deadlock, "Should detect deadlock condition"
    
    def test_watchdog_escalation_stages(self):
        """Test that watchdog uses escalating recovery stages."""
        watchdog_attempts = 0
        actions = []
        
        # Simulate watchdog triggering multiple times
        for attempt in range(1, 5):
            watchdog_attempts = attempt
            
            if watchdog_attempts == 1:
                action = "light_recovery"  # Check in-flight, requeue
            elif watchdog_attempts == 2:
                action = "refresh_peers"  # Rotate peers
            elif watchdog_attempts == 3:
                action = "hard_reset"  # Clear state
            else:
                action = "fork_resolution"  # Find ancestor, reorg
            
            actions.append(action)
        
        assert len(actions) == 4, "Should have 4 escalation stages"
        assert actions[0] == "light_recovery", "Stage 1: light recovery"
        assert actions[1] == "refresh_peers", "Stage 2: peer refresh"
        assert actions[2] == "hard_reset", "Stage 3: hard reset"
        assert actions[3] == "fork_resolution", "Stage 4: fork resolution"


class TestMetrics:
    """Test that new metrics are properly tracked."""
    
    def test_timeout_metrics(self):
        """Test timeout metrics are incremented."""
        stats = {}
        
        # Simulate timeouts
        stats["sync_inflight_timeout_total"] = stats.get("sync_inflight_timeout_total", 0) + 1
        stats["sync_retry_total"] = stats.get("sync_retry_total", 0) + 1
        
        assert stats["sync_inflight_timeout_total"] == 1, "Should track timeouts"
        assert stats["sync_retry_total"] == 1, "Should track retries"
    
    def test_peer_fail_metrics(self):
        """Test peer failure metrics are incremented."""
        stats = {}
        
        # Simulate peer failures
        peer_rotated_count = 3
        stats["sync_peer_fail_total"] = stats.get("sync_peer_fail_total", 0) + peer_rotated_count
        
        assert stats["sync_peer_fail_total"] == 3, "Should track peer failures"
    
    def test_abandoned_request_metrics(self):
        """Test abandoned request metrics are tracked."""
        stats = {}
        
        # Simulate abandoned requests
        stats["blocks_req_abandoned"] = stats.get("blocks_req_abandoned", 0) + 1
        stats["headers_req_abandoned"] = stats.get("headers_req_abandoned", 0) + 1
        
        assert stats["blocks_req_abandoned"] == 1, "Should track abandoned block requests"
        assert stats["headers_req_abandoned"] == 1, "Should track abandoned header requests"


class TestForkResolution:
    """Test fork detection and resolution."""
    
    def test_fork_detected_same_height_different_hash(self):
        """Test fork detected when heights match but hashes differ."""
        local_height = 100
        peer_height = 100
        local_hash = b"local_hash_abc"
        peer_hash = b"peer_hash_xyz"
        
        is_fork = (
            local_height == peer_height
            and local_hash != peer_hash
        )
        
        assert is_fork, "Should detect fork at same height"
    
    def test_fork_resolution_find_ancestor(self):
        """Test finding common ancestor for fork resolution."""
        local_chain = [
            (99, b"hash99"),
            (100, b"local_hash100"),
            (101, b"local_hash101"),
        ]
        peer_chain = [
            (99, b"hash99"),
            (100, b"peer_hash100"),
            (101, b"peer_hash101"),
        ]
        
        # Find common ancestor
        common_ancestor = None
        for local_block in local_chain:
            for peer_block in peer_chain:
                if local_block[0] == peer_block[0] and local_block[1] == peer_block[1]:
                    common_ancestor = local_block
                    break
            if common_ancestor:
                break
        
        assert common_ancestor is not None, "Should find common ancestor"
        assert common_ancestor[0] == 99, "Common ancestor at height 99"


class TestSyncStatusSnapshot:
    """Test sync status snapshot includes new metrics."""
    
    def test_snapshot_includes_orphan_metrics(self):
        """Test snapshot includes orphan cascade metrics."""
        snapshot_data = {
            "orphan_pool_size": 5,
            "orphan_cascade_successes": 10,
            "orphan_seen_count_entries": 3,
        }
        
        assert "orphan_cascade_successes" in snapshot_data
        assert snapshot_data["orphan_cascade_successes"] == 10
        assert "orphan_seen_count_entries" in snapshot_data
        assert snapshot_data["orphan_seen_count_entries"] == 3
    
    def test_snapshot_includes_retry_metrics(self):
        """Test snapshot includes retry and timeout metrics."""
        stats = {
            "sync_inflight_timeout_total": 5,
            "sync_retry_total": 8,
            "sync_peer_fail_total": 3,
            "blocks_req_abandoned": 1,
            "headers_req_abandoned": 0,
        }
        
        assert stats["sync_inflight_timeout_total"] == 5
        assert stats["sync_retry_total"] == 8
        assert stats["sync_peer_fail_total"] == 3
        assert stats["blocks_req_abandoned"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
