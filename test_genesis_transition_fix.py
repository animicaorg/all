"""
Test for genesis → height 1 block sync transition fix.

This test verifies that blocks at height 1 can be enqueued and imported
when the node is at genesis (height 0) and the parent is genesis hash.
"""

import asyncio
from unittest.mock import Mock, MagicMock


def test_genesis_to_height_1_enqueue():
    """Test that height 1 blocks can be enqueued when at genesis."""
    from p2p.node.p2p_service import P2PService
    
    # Create a minimal mock service
    service = Mock(spec=P2PService)
    service._genesis_hash = Mock(return_value=b"\x00" * 32)
    service._local_head = Mock(return_value=(0, None))  # At genesis
    service._has_block = Mock(return_value=False)
    service._has_header = Mock(return_value=False)
    service._sync_inflight_blocks = {}
    service._sync_block_buffer = {}
    service._sync_block_queue_set = set()
    service._sync_block_queue = []
    service._sync_block_queue_heights = {}
    service._sync_headers = {}
    service._sync_wakeup = Mock()
    service._sync_wakeup.set = Mock()
    
    # Create a mock header for height 1 with genesis parent
    mock_header = Mock()
    mock_header.height = 1
    mock_header.hash = b"\x01" * 32
    mock_header.parent_hash = b"\x00" * 32  # Genesis hash
    
    # Call the real method with mocks
    from p2p.node.p2p_service import P2PService
    result = P2PService._enqueue_missing_blocks(service, [mock_header])
    
    # Verify the block was added to the queue
    assert result == 1, f"Expected 1 block added, got {result}"
    assert len(service._sync_block_queue) == 1
    assert service._sync_block_queue[0] == mock_header.hash
    assert mock_header.hash in service._sync_block_queue_set
    assert service._sync_block_queue_heights[mock_header.hash] == 1
    assert service._sync_wakeup.set.called
    
    print("✓ Genesis → height 1 block enqueue test passed")


def test_genesis_to_height_1_not_enqueued_without_genesis_parent():
    """Test that height 1 blocks with non-genesis parent are not enqueued at genesis."""
    from p2p.node.p2p_service import P2PService
    
    # Create a minimal mock service
    service = Mock(spec=P2PService)
    service._genesis_hash = Mock(return_value=b"\x00" * 32)
    service._local_head = Mock(return_value=(0, None))  # At genesis
    service._has_block = Mock(return_value=False)
    service._has_header = Mock(return_value=False)
    service._sync_inflight_blocks = {}
    service._sync_block_buffer = {}
    service._sync_block_queue_set = set()
    service._sync_block_queue = []
    service._sync_block_queue_heights = {}
    service._sync_headers = {}
    service._sync_wakeup = Mock()
    service._sync_wakeup.set = Mock()
    
    # Create a mock header for height 1 with DIFFERENT parent (not genesis)
    mock_header = Mock()
    mock_header.height = 1
    mock_header.hash = b"\x01" * 32
    mock_header.parent_hash = b"\xFF" * 32  # NOT genesis hash
    
    # Call the real method with mocks
    from p2p.node.p2p_service import P2PService
    result = P2PService._enqueue_missing_blocks(service, [mock_header])
    
    # Verify the block was NOT added (small gap, parent not available)
    assert result == 0, f"Expected 0 blocks added, got {result}"
    assert len(service._sync_block_queue) == 0
    
    print("✓ Height 1 with non-genesis parent not enqueued test passed")


def test_height_2_requires_height_1_parent():
    """Test that height 2 blocks require height 1 parent to be available."""
    from p2p.node.p2p_service import P2PService
    
    # Create a minimal mock service
    service = Mock(spec=P2PService)
    service._genesis_hash = Mock(return_value=b"\x00" * 32)
    service._local_head = Mock(return_value=(1, None))  # At height 1
    service._has_block = Mock(return_value=False)
    service._has_header = Mock(return_value=False)
    service._sync_inflight_blocks = {}
    service._sync_block_buffer = {}
    service._sync_block_queue_set = set()
    service._sync_block_queue = []
    service._sync_block_queue_heights = {}
    service._sync_headers = {}
    service._sync_wakeup = Mock()
    service._sync_wakeup.set = Mock()
    
    # Create a mock header for height 2 with height 1 parent
    mock_header = Mock()
    mock_header.height = 2
    mock_header.hash = b"\x02" * 32
    mock_header.parent_hash = b"\x01" * 32  # Height 1 block
    
    # Parent not available - should not enqueue
    result = P2PService._enqueue_missing_blocks(service, [mock_header])
    assert result == 0, f"Expected 0 blocks added without parent, got {result}"
    
    # Now make parent available in queue
    service._sync_block_queue_set.add(b"\x01" * 32)
    
    result = P2PService._enqueue_missing_blocks(service, [mock_header])
    assert result == 1, f"Expected 1 block added with parent in queue, got {result}"
    
    print("✓ Height 2 requires height 1 parent test passed")


if __name__ == "__main__":
    test_genesis_to_height_1_enqueue()
    test_genesis_to_height_1_not_enqueued_without_genesis_parent()
    test_height_2_requires_height_1_parent()
    print("\n✅ All genesis transition tests passed!")
