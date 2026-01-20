"""
Test for genesis block enqueue fix.

This test verifies that when syncing from genesis:
1. Genesis header (height 0) is received
2. Genesis block body can be enqueued even when local_height == 0
3. This prevents sync deadlock where "sees headers but remains at genesis"

Issue: Syncing is broken it remains in genesis even though it sees the headers
"""

import asyncio
from unittest.mock import Mock, MagicMock


def test_genesis_block_enqueued_when_at_genesis():
    """
    Test that genesis block (height 0) can be enqueued when local_height == 0
    and the block body isn't present yet.
    
    This is the core fix for: "Syncing is broken it remains in genesis even though it sees the headers"
    """
    from p2p.node.p2p_service_legacy import P2PService
    
    # Create a minimal mock service
    service = Mock(spec=P2PService)
    service._genesis_hash = Mock(return_value=b"\x00" * 32)
    service._local_head = Mock(return_value=(0, None))  # At genesis height
    service._has_block = Mock(return_value=False)  # Genesis block body NOT present
    service._has_header = Mock(return_value=True)  # But header is present
    service._sync_inflight_blocks = {}
    service._sync_block_buffer = {}
    service._sync_block_queue_set = set()
    service._sync_block_queue = []
    service._sync_block_queue_heights = {}
    service._sync_headers = {}
    service._sync_wakeup = Mock()
    service._sync_wakeup.set = Mock()
    service._sync_trace = Mock()
    
    # Create a mock header for GENESIS (height 0)
    genesis_header = Mock()
    genesis_header.height = 0
    genesis_header.hash = b"\x00" * 32
    genesis_header.parent_hash = b"\x00" * 32  # Genesis has no real parent
    
    # Call the real method with mocks
    result = P2PService._enqueue_missing_blocks(service, [genesis_header])
    
    # CRITICAL: Genesis block should be enqueued even when local_height == 0
    # if the block body isn't present yet
    assert result == 1, f"Expected 1 block added (genesis), got {result}"
    assert len(service._sync_block_queue) == 1, "Genesis block should be in queue"
    assert service._sync_block_queue[0] == genesis_header.hash
    assert genesis_header.hash in service._sync_block_queue_set
    
    print("✓ Genesis block enqueued when at genesis height (fixes sync deadlock)")


def test_genesis_block_not_enqueued_if_already_present():
    """Test that genesis block is NOT enqueued if body is already present."""
    from p2p.node.p2p_service_legacy import P2PService
    
    service = Mock(spec=P2PService)
    service._genesis_hash = Mock(return_value=b"\x00" * 32)
    service._local_head = Mock(return_value=(0, None))
    service._has_block = Mock(return_value=True)  # Genesis block IS present
    service._has_header = Mock(return_value=True)
    service._sync_inflight_blocks = {}
    service._sync_block_buffer = {}
    service._sync_block_queue_set = set()
    service._sync_block_queue = []
    service._sync_block_queue_heights = {}
    service._sync_headers = {}
    service._sync_wakeup = Mock()
    service._sync_wakeup.set = Mock()
    service._sync_trace = Mock()
    
    genesis_header = Mock()
    genesis_header.height = 0
    genesis_header.hash = b"\x00" * 32
    genesis_header.parent_hash = b"\x00" * 32
    
    result = P2PService._enqueue_missing_blocks(service, [genesis_header])
    
    # Should NOT enqueue if block is already present
    assert result == 0, f"Expected 0 blocks added (genesis present), got {result}"
    assert len(service._sync_block_queue) == 0
    
    print("✓ Genesis block NOT enqueued if already present")


def test_height_1_enqueued_after_genesis():
    """
    Test complete flow: genesis enqueued, then height 1 can be enqueued.
    
    Simulates the full sync scenario where headers are received but blocks
    need to be downloaded.
    """
    from p2p.node.p2p_service_legacy import P2PService
    
    service = Mock(spec=P2PService)
    service._genesis_hash = Mock(return_value=b"\x00" * 32)
    service._local_head = Mock(return_value=(0, None))
    service._has_block = Mock(return_value=False)
    service._has_header = Mock(return_value=True)
    service._sync_inflight_blocks = {}
    service._sync_block_buffer = {}
    service._sync_block_queue_set = set()
    service._sync_block_queue = []
    service._sync_block_queue_heights = {}
    service._sync_headers = {}
    service._sync_wakeup = Mock()
    service._sync_wakeup.set = Mock()
    service._sync_trace = Mock()
    
    # Headers for genesis and height 1
    genesis_header = Mock()
    genesis_header.height = 0
    genesis_header.hash = b"\x00" * 32
    genesis_header.parent_hash = b"\x00" * 32
    
    height_1_header = Mock()
    height_1_header.height = 1
    height_1_header.hash = b"\x01" * 32
    height_1_header.parent_hash = b"\x00" * 32  # Genesis parent
    
    # Enqueue genesis first
    result = P2PService._enqueue_missing_blocks(service, [genesis_header])
    assert result == 1, "Genesis should be enqueued"
    
    # Now enqueue height 1 - parent (genesis) should be in queue, so it should work
    result = P2PService._enqueue_missing_blocks(service, [height_1_header])
    assert result == 1, "Height 1 should be enqueued after genesis"
    assert len(service._sync_block_queue) == 2
    
    print("✓ Complete sync flow: genesis → height 1")


def test_non_genesis_blocks_still_filtered_correctly():
    """Ensure fix doesn't break normal height filtering."""
    from p2p.node.p2p_service_legacy import P2PService
    
    service = Mock(spec=P2PService)
    service._genesis_hash = Mock(return_value=b"\x00" * 32)
    service._local_head = Mock(return_value=(5, None))  # At height 5
    service._has_block = Mock(return_value=False)
    service._has_header = Mock(return_value=True)
    service._sync_inflight_blocks = {}
    service._sync_block_buffer = {}
    service._sync_block_queue_set = set()
    service._sync_block_queue = []
    service._sync_block_queue_heights = {}
    service._sync_headers = {b"\x05" * 32: Mock(height=5)}
    service._sync_wakeup = Mock()
    service._sync_wakeup.set = Mock()
    service._sync_trace = Mock()
    
    # Try to enqueue height 5 (should be skipped - at local height)
    height_5_header = Mock()
    height_5_header.height = 5
    height_5_header.hash = b"\x05" * 32
    height_5_header.parent_hash = b"\x04" * 32
    
    result = P2PService._enqueue_missing_blocks(service, [height_5_header])
    assert result == 0, "Height 5 should be skipped when local_height == 5"
    
    # Height 4 should also be skipped (< local_height)
    height_4_header = Mock()
    height_4_header.height = 4
    height_4_header.hash = b"\x04" * 32
    height_4_header.parent_hash = b"\x03" * 32
    
    result = P2PService._enqueue_missing_blocks(service, [height_4_header])
    assert result == 0, "Height 4 should be skipped when local_height == 5"
    
    print("✓ Non-genesis blocks filtered correctly")


if __name__ == "__main__":
    test_genesis_block_enqueued_when_at_genesis()
    test_genesis_block_not_enqueued_if_already_present()
    test_height_1_enqueued_after_genesis()
    test_non_genesis_blocks_still_filtered_correctly()
    print("\n✅ All genesis sync block enqueue tests passed!")
