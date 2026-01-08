#!/usr/bin/env python3
"""Test that snapshot directory resolution is consistent."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add repo to path
sys.path.insert(0, str(Path(__file__).parent))


def test_snapshot_dir_resolution():
    """Test that RPC snapshot methods use correct data_root."""
    from rpc import deps
    from rpc.methods import snapshot
    
    # Use temp directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock context with data_root
        mock_ctx = MagicMock()
        mock_ctx.data_root = Path(tmpdir) / ".animica" / "chain-1"
    # Use temp directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock context with data_root
        mock_ctx = MagicMock()
        mock_ctx.data_root = Path(tmpdir) / ".animica" / "chain-1"
        
        with patch.object(deps, 'get_ctx', return_value=mock_ctx):
            snapshots_dir = snapshot._get_snapshots_dir()
            
            # Should use parent directory since data_root ends with chain-1
            expected = Path(tmpdir) / ".animica" / "snapshots"
            print(f"Snapshots dir: {snapshots_dir}")
            print(f"Expected: {expected}")
            assert snapshots_dir == expected, f"Expected {expected}, got {snapshots_dir}"
        
        print("✅ Test 1 passed: chain-specific data_root uses parent directory")
        
        # Test with non-chain-specific data_root
        mock_ctx.data_root = Path(tmpdir) / "animica"
        
        with patch.object(deps, 'get_ctx', return_value=mock_ctx):
            snapshots_dir = snapshot._get_snapshots_dir()
            
            # Should use data_root directly
            expected = Path(tmpdir) / "animica" / "snapshots"
            print(f"Snapshots dir: {snapshots_dir}")
            print(f"Expected: {expected}")
            assert snapshots_dir == expected, f"Expected {expected}, got {snapshots_dir}"
        
        print("✅ Test 2 passed: custom data_root uses directory directly")


def test_snapshot_checkpoint_dir():
    """Test that checkpoint snapshot directories include chain ID."""
    from rpc import deps
    from rpc.methods import snapshot
    
    # Use temp directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock context
        mock_ctx = MagicMock()
        mock_ctx.data_root = Path(tmpdir) / ".animica" / "chain-1"
        
        with patch.object(deps, 'get_ctx', return_value=mock_ctx):
            checkpoint_dir = snapshot._get_checkpoint_snapshots_dir(chain_id=1, checkpoint_height=2000)
            
            # Should be: tmpdir/.animica/snapshots/chain-1-height-2000
            expected = Path(tmpdir) / ".animica" / "snapshots" / "chain-1-height-2000"
            print(f"Checkpoint dir: {checkpoint_dir}")
            print(f"Expected: {expected}")
            assert checkpoint_dir == expected, f"Expected {expected}, got {checkpoint_dir}"
        
        print("✅ Test 3 passed: checkpoint directory includes chain ID and height")


if __name__ == "__main__":
    print("Testing snapshot directory resolution fix...\n")
    test_snapshot_dir_resolution()
    print()
    test_snapshot_checkpoint_dir()
    print("\n✅ All tests passed!")
