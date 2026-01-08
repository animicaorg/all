"""
Integration test for continuous snapshot discovery functionality.

Tests that snapshot discovery retries continuously until a snapshot
is found and imported, or the node is synced.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_continuous_snapshot_discovery_retries():
    """Test that continuous discovery retries until snapshot is found."""
    
    from p2p.sync.snapshot_sync import continuous_snapshot_discovery
    
    mock_block_db = MagicMock()
    mock_block_db.get_head.return_value = (0, b"\x00" * 32)
    
    mock_state_db = MagicMock()
    mock_p2p_service = MagicMock()
    
    # Track retry attempts
    retry_count = [0]
    
    # Set environment for fast retries in tests
    os.environ["ANIMICA_SNAPSHOT_SYNC_ENABLED"] = "true"
    os.environ["ANIMICA_SNAPSHOT_MIN_HEIGHT"] = "1000"
    os.environ["ANIMICA_SNAPSHOT_RETRY_INTERVAL"] = "0.1"  # Fast retry for test
    os.environ["ANIMICA_SNAPSHOT_MAX_RETRIES"] = "3"
    
    try:
        # Mock try_snapshot_bootstrap to fail first 2 times, succeed on 3rd
        async def mock_bootstrap(*args, **kwargs):
            retry_count[0] += 1
            if retry_count[0] < 3:
                return (False, "No snapshots available")
            return (True, None)
        
        with patch("p2p.sync.snapshot_sync.try_snapshot_bootstrap", side_effect=mock_bootstrap):
            # Run continuous discovery
            await continuous_snapshot_discovery(
                block_db=mock_block_db,
                state_db=mock_state_db,
                chain_id=1,
                p2p_service=mock_p2p_service,
            )
        
        # Should have retried 3 times (2 failures + 1 success)
        assert retry_count[0] == 3
        
    finally:
        for key in ["ANIMICA_SNAPSHOT_SYNC_ENABLED", "ANIMICA_SNAPSHOT_MIN_HEIGHT", 
                    "ANIMICA_SNAPSHOT_RETRY_INTERVAL", "ANIMICA_SNAPSHOT_MAX_RETRIES"]:
            os.environ.pop(key, None)


@pytest.mark.asyncio
async def test_continuous_discovery_stops_on_max_retries():
    """Test that continuous discovery respects max retry limit."""
    
    from p2p.sync.snapshot_sync import continuous_snapshot_discovery
    
    mock_block_db = MagicMock()
    mock_block_db.get_head.return_value = (0, b"\x00" * 32)
    
    mock_state_db = MagicMock()
    mock_p2p_service = MagicMock()
    
    retry_count = [0]
    
    os.environ["ANIMICA_SNAPSHOT_SYNC_ENABLED"] = "true"
    os.environ["ANIMICA_SNAPSHOT_MIN_HEIGHT"] = "1000"
    os.environ["ANIMICA_SNAPSHOT_RETRY_INTERVAL"] = "0.1"
    os.environ["ANIMICA_SNAPSHOT_MAX_RETRIES"] = "5"
    
    try:
        # Mock bootstrap to always fail
        async def mock_bootstrap(*args, **kwargs):
            retry_count[0] += 1
            return (False, "No snapshots available")
        
        with patch("p2p.sync.snapshot_sync.try_snapshot_bootstrap", side_effect=mock_bootstrap):
            await continuous_snapshot_discovery(
                block_db=mock_block_db,
                state_db=mock_state_db,
                chain_id=1,
                p2p_service=mock_p2p_service,
            )
        
        # Should have stopped after exactly 5 retries
        assert retry_count[0] == 5
        
    finally:
        for key in ["ANIMICA_SNAPSHOT_SYNC_ENABLED", "ANIMICA_SNAPSHOT_MIN_HEIGHT",
                    "ANIMICA_SNAPSHOT_RETRY_INTERVAL", "ANIMICA_SNAPSHOT_MAX_RETRIES"]:
            os.environ.pop(key, None)


@pytest.mark.asyncio
async def test_continuous_discovery_stops_when_synced():
    """Test that continuous discovery stops when node reaches sync threshold."""
    
    from p2p.sync.snapshot_sync import continuous_snapshot_discovery
    
    mock_block_db = MagicMock()
    # Start low, then advance height on each check
    heights = [0, 500, 1500]
    height_index = [0]
    
    def get_head():
        idx = height_index[0]
        height_index[0] += 1
        if idx < len(heights):
            return (heights[idx], b"\x00" * 32)
        return (heights[-1], b"\x00" * 32)
    
    mock_block_db.get_head = get_head
    
    mock_state_db = MagicMock()
    mock_p2p_service = MagicMock()
    
    retry_count = [0]
    
    os.environ["ANIMICA_SNAPSHOT_SYNC_ENABLED"] = "true"
    os.environ["ANIMICA_SNAPSHOT_MIN_HEIGHT"] = "1000"
    os.environ["ANIMICA_SNAPSHOT_RETRY_INTERVAL"] = "0.1"
    os.environ["ANIMICA_SNAPSHOT_MAX_RETRIES"] = "0"  # Unlimited
    
    try:
        async def mock_bootstrap(*args, **kwargs):
            retry_count[0] += 1
            return (False, "No snapshots available")
        
        with patch("p2p.sync.snapshot_sync.try_snapshot_bootstrap", side_effect=mock_bootstrap):
            await continuous_snapshot_discovery(
                block_db=mock_block_db,
                state_db=mock_state_db,
                chain_id=1,
                p2p_service=mock_p2p_service,
            )
        
        # Should have stopped when height reached 1500 (above threshold)
        # Might be 2 or 3 attempts depending on timing
        assert 1 <= retry_count[0] <= 3
        
    finally:
        for key in ["ANIMICA_SNAPSHOT_SYNC_ENABLED", "ANIMICA_SNAPSHOT_MIN_HEIGHT",
                    "ANIMICA_SNAPSHOT_RETRY_INTERVAL", "ANIMICA_SNAPSHOT_MAX_RETRIES"]:
            os.environ.pop(key, None)


@pytest.mark.asyncio
async def test_continuous_discovery_respects_stop_event():
    """Test that continuous discovery can be stopped via event."""
    
    from p2p.sync.snapshot_sync import continuous_snapshot_discovery
    
    mock_block_db = MagicMock()
    mock_block_db.get_head.return_value = (0, b"\x00" * 32)
    
    mock_state_db = MagicMock()
    mock_p2p_service = MagicMock()
    
    retry_count = [0]
    stop_event = asyncio.Event()
    
    os.environ["ANIMICA_SNAPSHOT_SYNC_ENABLED"] = "true"
    os.environ["ANIMICA_SNAPSHOT_MIN_HEIGHT"] = "1000"
    os.environ["ANIMICA_SNAPSHOT_RETRY_INTERVAL"] = "1.0"  # Longer interval
    os.environ["ANIMICA_SNAPSHOT_MAX_RETRIES"] = "0"  # Unlimited
    
    try:
        async def mock_bootstrap(*args, **kwargs):
            retry_count[0] += 1
            return (False, "No snapshots available")
        
        with patch("p2p.sync.snapshot_sync.try_snapshot_bootstrap", side_effect=mock_bootstrap):
            # Start discovery in background
            task = asyncio.create_task(
                continuous_snapshot_discovery(
                    block_db=mock_block_db,
                    state_db=mock_state_db,
                    chain_id=1,
                    p2p_service=mock_p2p_service,
                    stop_event=stop_event,
                )
            )
            
            # Let it run for a short time
            await asyncio.sleep(0.3)
            
            # Signal stop
            stop_event.set()
            
            # Wait for task to complete
            await asyncio.wait_for(task, timeout=2.0)
        
        # Should have stopped quickly after event was set
        # At most 1 or 2 attempts before stopping
        assert 1 <= retry_count[0] <= 2
        
    finally:
        for key in ["ANIMICA_SNAPSHOT_SYNC_ENABLED", "ANIMICA_SNAPSHOT_MIN_HEIGHT",
                    "ANIMICA_SNAPSHOT_RETRY_INTERVAL", "ANIMICA_SNAPSHOT_MAX_RETRIES"]:
            os.environ.pop(key, None)


@pytest.mark.asyncio
async def test_continuous_discovery_handles_exceptions():
    """Test that continuous discovery handles errors gracefully."""
    
    from p2p.sync.snapshot_sync import continuous_snapshot_discovery
    
    mock_block_db = MagicMock()
    mock_block_db.get_head.return_value = (0, b"\x00" * 32)
    
    mock_state_db = MagicMock()
    mock_p2p_service = MagicMock()
    
    retry_count = [0]
    
    os.environ["ANIMICA_SNAPSHOT_SYNC_ENABLED"] = "true"
    os.environ["ANIMICA_SNAPSHOT_MIN_HEIGHT"] = "1000"
    os.environ["ANIMICA_SNAPSHOT_RETRY_INTERVAL"] = "0.1"
    os.environ["ANIMICA_SNAPSHOT_MAX_RETRIES"] = "3"
    
    try:
        async def mock_bootstrap(*args, **kwargs):
            retry_count[0] += 1
            if retry_count[0] < 3:
                # Raise exception on first 2 attempts
                raise RuntimeError("Network error")
            return (True, None)
        
        with patch("p2p.sync.snapshot_sync.try_snapshot_bootstrap", side_effect=mock_bootstrap):
            # Should handle exceptions and continue
            await continuous_snapshot_discovery(
                block_db=mock_block_db,
                state_db=mock_state_db,
                chain_id=1,
                p2p_service=mock_p2p_service,
            )
        
        # Should have recovered from errors and succeeded
        assert retry_count[0] == 3
        
    finally:
        for key in ["ANIMICA_SNAPSHOT_SYNC_ENABLED", "ANIMICA_SNAPSHOT_MIN_HEIGHT",
                    "ANIMICA_SNAPSHOT_RETRY_INTERVAL", "ANIMICA_SNAPSHOT_MAX_RETRIES"]:
            os.environ.pop(key, None)


def test_snapshot_retry_environment_variables():
    """Test that retry configuration environment variables are properly read."""
    
    from p2p.sync.snapshot_sync import (
        _get_snapshot_retry_interval,
        _get_snapshot_max_retries,
    )
    
    # Test retry interval
    os.environ["ANIMICA_SNAPSHOT_RETRY_INTERVAL"] = "120"
    assert _get_snapshot_retry_interval() == 120.0
    
    os.environ["ANIMICA_SNAPSHOT_RETRY_INTERVAL"] = "30.5"
    assert _get_snapshot_retry_interval() == 30.5
    
    # Test max retries
    os.environ["ANIMICA_SNAPSHOT_MAX_RETRIES"] = "10"
    assert _get_snapshot_max_retries() == 10
    
    os.environ["ANIMICA_SNAPSHOT_MAX_RETRIES"] = "0"  # Unlimited
    assert _get_snapshot_max_retries() == 0
    
    # Test defaults
    os.environ.pop("ANIMICA_SNAPSHOT_RETRY_INTERVAL", None)
    os.environ.pop("ANIMICA_SNAPSHOT_MAX_RETRIES", None)
    
    assert _get_snapshot_retry_interval() == 60.0  # Default 60s
    assert _get_snapshot_max_retries() == 0  # Default unlimited
    
    # Cleanup
    for key in ["ANIMICA_SNAPSHOT_RETRY_INTERVAL", "ANIMICA_SNAPSHOT_MAX_RETRIES"]:
        os.environ.pop(key, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
