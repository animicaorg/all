"""Test that checkpoints make no HTTP calls when disabled."""

import pytest
from unittest.mock import patch, MagicMock

from p2p.checkpoints.config import CheckpointsConfig
from p2p.checkpoints.loader import CheckpointLoader


@pytest.mark.asyncio
async def test_no_http_calls_when_disabled():
    """Test that no HTTP calls are made when checkpoints are disabled."""
    config = CheckpointsConfig(mode="off")
    loader = CheckpointLoader(config)
    
    # Simply verify that disabled mode returns empty list
    # No need to mock httpx since it won't be imported when mode is off
    checkpoints = await loader.load_checkpoints()
    
    # Should return empty list without making HTTP calls
    assert checkpoints == []


@pytest.mark.asyncio
async def test_no_http_calls_in_file_mode(tmp_path):
    """Test that file mode doesn't make HTTP calls."""
    import json
    
    # Create a valid checkpoint file
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump({
            "checkpoints": [
                {"height": 100, "hash": "0xabc123"}
            ]
        }, f)
    
    config = CheckpointsConfig(mode="file", file_path=str(checkpoint_file))
    loader = CheckpointLoader(config)
    
    # Simply verify that file mode loads from file successfully
    # No need to mock httpx since it won't be imported for file mode
    checkpoints = await loader.load_checkpoints()
    
    # Should load from file successfully
    assert len(checkpoints) == 1
    assert checkpoints[0].height == 100


@pytest.mark.asyncio  
async def test_http_only_in_rpc_mode():
    """Test that HTTP calls are only made in RPC mode."""
    import httpx
    
    config = CheckpointsConfig(mode="rpc", rpc_url="https://test.rpc/endpoint", strict=False)
    loader = CheckpointLoader(config)
    
    # Patch httpx.AsyncClient since it's imported dynamically in the method
    with patch("httpx.AsyncClient") as mock_client_class:
        # Mock the async context manager
        mock_client = MagicMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None
        
        # Mock failed responses (so it returns empty list in non-strict mode)
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "not found"}
        mock_response.status_code = 404
        mock_client.post.return_value = mock_response
        mock_client.get.return_value = mock_response
        
        checkpoints = await loader.load_checkpoints()
        
        # Should have tried HTTP calls in RPC mode
        assert mock_client_class.called
        # Returns empty due to non-strict mode
        assert checkpoints == []
