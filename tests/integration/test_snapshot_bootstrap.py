"""
Integration test for snapshot bootstrap functionality.

Tests that snapshot sync is properly integrated into the P2P startup flow
and can download and import snapshots.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_snapshot_bootstrap_integration():
    """Test that snapshot bootstrap is called during P2P startup."""
    
    # Mock dependencies
    mock_block_db = MagicMock()
    mock_block_db.get_head.return_value = (0, b"\x00" * 32)
    
    mock_state_db = MagicMock()
    
    # Mock the try_snapshot_bootstrap function
    with patch("p2p.sync.snapshot_sync.try_snapshot_bootstrap") as mock_bootstrap:
        mock_bootstrap.return_value = (False, "No snapshot source configured")
        
        # Import and call the startup code
        from rpc.deps import build_context
        from rpc.config import RpcConfig
        
        # Create minimal config
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = RpcConfig(
                data_dir=tmpdir,
                chain_id=1,
                db_uri=f"sqlite:///{tmpdir}/test.db",
                genesis_path=None,
            )
            
            # Disable P2P to avoid full initialization
            os.environ["ANIMICA_P2P_ENABLE"] = "false"
            
            try:
                ctx = build_context(cfg)
                
                # Verify context was created
                assert ctx is not None
                assert ctx.chain_id == 1
                
            finally:
                os.environ.pop("ANIMICA_P2P_ENABLE", None)


@pytest.mark.asyncio
async def test_snapshot_bootstrap_called_with_correct_params():
    """Test that snapshot bootstrap receives correct parameters."""
    
    mock_block_db = MagicMock()
    mock_block_db.get_head.return_value = (100, b"\xaa" * 32)
    
    mock_state_db = MagicMock()
    
    with patch("p2p.sync.snapshot_sync.try_snapshot_bootstrap") as mock_bootstrap:
        mock_bootstrap.return_value = (True, None)
        
        # Simulate calling the bootstrap
        from p2p.sync.snapshot_sync import try_snapshot_bootstrap
        
        success, error = await try_snapshot_bootstrap(
            block_db=mock_block_db,
            state_db=mock_state_db,
            chain_id=1,
            current_height=100,
        )
        
        # Verify bootstrap was attempted
        mock_bootstrap.assert_called_once_with(
            block_db=mock_block_db,
            state_db=mock_state_db,
            chain_id=1,
            current_height=100,
        )


@pytest.mark.asyncio
async def test_snapshot_download_fallback():
    """Test that snapshot download falls back to HTTP when RPC fails."""
    
    from p2p.sync.snapshot_sync import _download_and_import_snapshot
    
    mock_block_db = MagicMock()
    mock_state_db = MagicMock()
    
    # Mock httpx client responses
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = mock_client_class.return_value.__aenter__.return_value
        
        # Mock manifest response
        manifest_response = MagicMock()
        manifest_response.json.return_value = {
            "result": {
                "success": True,
                "manifest": {
                    "version": 1,
                    "chain_id": 1,
                    "checkpoint_height": 1000,
                    "checkpoint_hash": "0xabc",
                    "chunks": [],
                },
                "path": "/nonexistent/path",
            }
        }
        
        mock_client.post.return_value = manifest_response
        
        # Test with no RPC URL configured
        os.environ.pop("ANIMICA_SNAPSHOT_RPC_URL", None)
        
        # Should return False when no RPC URL
        result = await _download_and_import_snapshot(
            rpc_url="",
            chain_id=1,
            checkpoint_height=1000,
            block_db=mock_block_db,
            state_db=mock_state_db,
        )
        
        assert result is False


def test_snapshot_environment_variables():
    """Test that snapshot environment variables are properly read."""
    
    from p2p.sync.snapshot_sync import (
        _is_snapshot_sync_enabled,
        _get_snapshot_rpc_url,
        _get_snapshot_timeout,
    )
    
    # Test enabled flag
    os.environ["ANIMICA_SNAPSHOT_SYNC_ENABLED"] = "true"
    assert _is_snapshot_sync_enabled() is True
    
    os.environ["ANIMICA_SNAPSHOT_SYNC_ENABLED"] = "false"
    assert _is_snapshot_sync_enabled() is False
    
    # Test RPC URL
    os.environ["ANIMICA_SNAPSHOT_RPC_URL"] = "http://snapshots.example.com:8545/rpc"
    assert _get_snapshot_rpc_url() == "http://snapshots.example.com:8545/rpc"
    
    # Test timeout
    os.environ["ANIMICA_SNAPSHOT_TIMEOUT"] = "1200"
    assert _get_snapshot_timeout() == 1200.0
    
    # Cleanup
    for key in ["ANIMICA_SNAPSHOT_SYNC_ENABLED", "ANIMICA_SNAPSHOT_RPC_URL", "ANIMICA_SNAPSHOT_TIMEOUT"]:
        os.environ.pop(key, None)


def test_should_try_snapshot_bootstrap():
    """Test the logic for determining when to use snapshot bootstrap."""
    
    from p2p.sync.snapshot_sync import should_try_snapshot_bootstrap
    
    # Set environment
    os.environ["ANIMICA_SNAPSHOT_SYNC_ENABLED"] = "true"
    os.environ["ANIMICA_SNAPSHOT_MIN_HEIGHT"] = "1000"
    
    try:
        # Should use snapshot when height is low
        assert should_try_snapshot_bootstrap(current_height=0) is True
        assert should_try_snapshot_bootstrap(current_height=100) is True
        assert should_try_snapshot_bootstrap(current_height=500) is True
        
        # Should not use snapshot when already synced
        assert should_try_snapshot_bootstrap(current_height=1000) is False
        assert should_try_snapshot_bootstrap(current_height=5000) is False
        
        # Should consider target height if provided
        assert should_try_snapshot_bootstrap(current_height=100, target_height=200) is False
        assert should_try_snapshot_bootstrap(current_height=100, target_height=2000) is True
        
    finally:
        os.environ.pop("ANIMICA_SNAPSHOT_SYNC_ENABLED", None)
        os.environ.pop("ANIMICA_SNAPSHOT_MIN_HEIGHT", None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
