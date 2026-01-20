"""
Integration scenario test for genesis sync fix.

This simulates the exact scenario from the issue:
"Syncing is broken it remains in genesis even though it sees the headers"

Scenario:
1. Node starts with genesis finalized (height 0)
2. P2P receives headers for heights 0, 1, 2, 3
3. Headers are successfully stored
4. Block sync should enqueue blocks starting from genesis
5. Before fix: Genesis skipped, height 1+ can't be enqueued (deadlock)
6. After fix: Genesis enqueued, then height 1+, sync progresses

This is an end-to-end verification that the fix resolves the issue.
"""

from unittest.mock import Mock


def test_integration_genesis_sync_scenario():
    """
    End-to-end test: Node sees headers but was stuck at genesis.
    
    Simulates:
    - Node finalized genesis (height 0)
    - Headers synced for 0, 1, 2, 3
    - Block sync needs to download bodies
    - Before fix: deadlock at genesis
    - After fix: sync progresses
    """
    from p2p.node.p2p_service_legacy import P2PService
    
    # Setup mock service at genesis
    service = Mock(spec=P2PService)
    service._genesis_hash = Mock(return_value=b"\x00" * 32)
    service._local_head = Mock(return_value=(0, b"\x00" * 32))  # At genesis
    service._sync_inflight_blocks = {}
    service._sync_block_buffer = {}
    service._sync_block_queue_set = set()
    service._sync_block_queue = []
    service._sync_block_queue_heights = {}
    service._sync_headers = {}
    service._sync_wakeup = Mock()
    service._sync_wakeup.set = Mock()
    service._sync_trace = Mock()
    
    # Mock _has_header to return True (headers synced)
    # Mock _has_block to return False (blocks NOT synced)
    service._has_header = Mock(return_value=True)
    service._has_block = Mock(return_value=False)
    
    # Create headers for 0, 1, 2, 3
    headers = []
    for i in range(4):
        hdr = Mock()
        hdr.height = i
        hdr.hash = bytes([i]) * 32
        hdr.parent_hash = bytes([i-1]) * 32 if i > 0 else b"\x00" * 32
        headers.append(hdr)
    
    print("=" * 60)
    print("SCENARIO: Node sees headers but stuck at genesis")
    print("=" * 60)
    print(f"Initial state: local_height=0 (genesis)")
    print(f"Headers received: heights 0, 1, 2, 3 (header sync OK)")
    print(f"Block bodies: MISSING (need to download)")
    print()
    
    # Enqueue blocks for download
    result = P2PService._enqueue_missing_blocks(service, headers)
    
    print(f"Block enqueue result: {result} blocks queued")
    print(f"Queue contents: {len(service._sync_block_queue)} blocks")
    print(f"Queue heights: {[service._sync_block_queue_heights.get(h, '?') for h in service._sync_block_queue]}")
    print()
    
    # Verify fix: All 4 blocks should be enqueued
    assert result == 4, f"Expected 4 blocks enqueued, got {result}"
    assert len(service._sync_block_queue) == 4, "All blocks should be in queue"
    
    # Verify order: genesis first, then 1, 2, 3
    expected_heights = [0, 1, 2, 3]
    actual_heights = [service._sync_block_queue_heights.get(h, -1) for h in service._sync_block_queue]
    assert actual_heights == expected_heights, f"Expected {expected_heights}, got {actual_heights}"
    
    print("✅ FIX VERIFIED:")
    print("   - Genesis block (height 0) enqueued despite local_height == 0")
    print("   - Height 1, 2, 3 enqueued with correct parent ordering")
    print("   - Sync can now progress (no deadlock)")
    print()
    
    # Verify the specific issue is fixed
    genesis_in_queue = service._sync_block_queue[0]
    genesis_height = service._sync_block_queue_heights[genesis_in_queue]
    assert genesis_height == 0, "Genesis must be first in queue"
    
    print("✅ ISSUE FIXED: 'Syncing is broken it remains in genesis even though it sees the headers'")
    print("   Root cause: Genesis block wasn't being enqueued when local_height == 0")
    print("   Solution: Allow genesis (height 0) to be enqueued at genesis height")
    print("=" * 60)


if __name__ == "__main__":
    test_integration_genesis_sync_scenario()
    print("\n✅ Genesis sync integration test passed!")
