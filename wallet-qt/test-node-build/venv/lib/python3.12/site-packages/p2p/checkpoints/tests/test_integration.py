"""Tests for checkpoint integration."""

import json
import pytest

from p2p.checkpoints import (
    initialize_checkpoints,
    get_checkpoint_config_summary,
    verify_chain_checkpoints,
)
from p2p.checkpoints.config import CheckpointsConfig
from p2p.checkpoints.verifier import CheckpointVerifier
from p2p.checkpoints.loader import Checkpoint


@pytest.mark.asyncio
async def test_initialize_checkpoints_disabled():
    """Test initializing checkpoints when disabled."""
    config = CheckpointsConfig(mode="off")
    
    verifier = await initialize_checkpoints(config)
    
    assert verifier is None


@pytest.mark.asyncio
async def test_initialize_checkpoints_from_file(tmp_path):
    """Test initializing checkpoints from file."""
    # Create checkpoint file
    checkpoint_data = {
        "checkpoints": [
            {"height": 100, "hash": "0xabc123"},
            {"height": 200, "hash": "0xdef456"},
        ]
    }
    
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    config = CheckpointsConfig(mode="file", file_path=str(checkpoint_file))
    
    verifier = await initialize_checkpoints(config)
    
    assert verifier is not None
    assert verifier.has_checkpoints()
    assert verifier.get_lowest_checkpoint_height() == 100
    assert verifier.get_highest_checkpoint_height() == 200


@pytest.mark.asyncio
async def test_initialize_checkpoints_strict_mode_no_checkpoints(tmp_path):
    """Test that strict mode raises when no checkpoints available."""
    # Create empty checkpoint file
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump({"checkpoints": []}, f)
    
    config = CheckpointsConfig(
        mode="file", 
        file_path=str(checkpoint_file),
        strict=True
    )
    
    with pytest.raises(RuntimeError, match="Strict mode"):
        await initialize_checkpoints(config)


@pytest.mark.asyncio
async def test_initialize_checkpoints_non_strict_mode_no_checkpoints(tmp_path):
    """Test that non-strict mode returns None when no checkpoints available."""
    # Create empty checkpoint file
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump({"checkpoints": []}, f)
    
    config = CheckpointsConfig(
        mode="file", 
        file_path=str(checkpoint_file),
        strict=False
    )
    
    verifier = await initialize_checkpoints(config)
    
    assert verifier is None


def test_get_checkpoint_config_summary():
    """Test getting checkpoint config summary."""
    config = CheckpointsConfig(
        mode="rpc",
        rpc_url="https://test.rpc/endpoint",
        strict=True,
        max_age_seconds=3600,
    )
    
    summary = get_checkpoint_config_summary(config)
    
    assert summary["mode"] == "rpc"
    assert summary["enabled"] is True
    assert summary["rpc_url"] == "https://test.rpc/endpoint"
    assert summary["strict"] is True
    assert summary["max_age_seconds"] == 3600


@pytest.mark.asyncio
async def test_verify_chain_checkpoints_no_verifier():
    """Test verifying chain when verifier is None."""
    class MockChain:
        async def get_block_hash_at_height(self, height):
            return "0xabc123"
    
    is_valid, errors = await verify_chain_checkpoints(None, MockChain())
    
    assert is_valid
    assert errors == []


@pytest.mark.asyncio
async def test_verify_chain_checkpoints_with_verifier():
    """Test verifying chain with valid checkpoints."""
    checkpoints = [
        Checkpoint(height=100, hash="0xabc123"),
        Checkpoint(height=200, hash="0xdef456"),
    ]
    verifier = CheckpointVerifier(checkpoints)
    
    class MockChain:
        async def get_block_hash_at_height(self, height):
            if height == 100:
                return "0xabc123"
            elif height == 200:
                return "0xdef456"
            return None
    
    is_valid, errors = await verify_chain_checkpoints(verifier, MockChain())
    
    assert is_valid
    assert errors == []


@pytest.mark.asyncio
async def test_verify_chain_checkpoints_mismatch():
    """Test verifying chain with checkpoint mismatch."""
    checkpoints = [
        Checkpoint(height=100, hash="0xabc123"),
        Checkpoint(height=200, hash="0xdef456"),
    ]
    verifier = CheckpointVerifier(checkpoints)
    
    class MockChain:
        async def get_block_hash_at_height(self, height):
            if height == 100:
                return "0xabc123"
            elif height == 200:
                return "0xWRONG"  # Mismatch
            return None
    
    is_valid, errors = await verify_chain_checkpoints(verifier, MockChain())
    
    assert not is_valid
    assert len(errors) == 1
    assert "mismatch" in errors[0].lower()
