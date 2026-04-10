"""Tests for checkpoint integration with built-in checkpoints."""

import pytest

from p2p.checkpoints import initialize_checkpoints
from p2p.checkpoints.config import CheckpointsConfig


@pytest.mark.asyncio
async def test_initialize_checkpoints_mainnet_builtin():
    """Test that mainnet gets built-in checkpoint even in off mode."""
    config = CheckpointsConfig(mode="off")
    
    # With chain_id=1 and include_builtin=True (default), should get built-in checkpoints
    verifier = await initialize_checkpoints(config, chain_id=1, include_builtin=True)
    
    assert verifier is not None, "Should get verifier with built-in checkpoints"
    assert verifier.has_checkpoints(), "Should have checkpoints"
    
    # Check for the specific mainnet checkpoint
    cp = verifier.get_checkpoint_at_height(55795)
    assert cp is not None, "Should have checkpoint at height 55795"
    assert cp.hash == "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938"


@pytest.mark.asyncio
async def test_initialize_checkpoints_testnet_no_builtin():
    """Test that testnet gets no built-in checkpoints."""
    config = CheckpointsConfig(mode="off")
    
    # Testnet (chain_id=2) has no built-in checkpoints
    verifier = await initialize_checkpoints(config, chain_id=2, include_builtin=True)
    
    assert verifier is None, "Should get None when no checkpoints available"


@pytest.mark.asyncio
async def test_initialize_checkpoints_disabled_no_builtin():
    """Test that include_builtin=False disables built-in checkpoints."""
    config = CheckpointsConfig(mode="off")
    
    # Even with mainnet, if include_builtin=False, should get nothing
    verifier = await initialize_checkpoints(config, chain_id=1, include_builtin=False)
    
    assert verifier is None, "Should get None when built-in is disabled"


@pytest.mark.asyncio
async def test_initialize_checkpoints_no_chain_id():
    """Test that no chain_id means no built-in checkpoints."""
    config = CheckpointsConfig(mode="off")
    
    # Without chain_id, can't load built-in checkpoints
    verifier = await initialize_checkpoints(config, chain_id=None, include_builtin=True)
    
    assert verifier is None, "Should get None without chain_id"


@pytest.mark.asyncio
async def test_initialize_checkpoints_file_mode_with_builtin(tmp_path):
    """Test that file mode can be merged with built-in checkpoints."""
    import json
    
    # Create a checkpoint file with different heights
    checkpoint_data = {
        "checkpoints": [
            {"height": 1000, "hash": "0xabc123"},
            {"height": 2000, "hash": "0xdef456"},
        ]
    }
    
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    config = CheckpointsConfig(mode="file", file_path=str(checkpoint_file))
    
    # Should get both file checkpoints and built-in checkpoint
    verifier = await initialize_checkpoints(config, chain_id=1, include_builtin=True)
    
    assert verifier is not None
    assert verifier.has_checkpoints()
    
    # Should have all checkpoints
    heights = [cp.height for cp in verifier.checkpoints]
    assert 1000 in heights, "Should have checkpoint from file"
    assert 2000 in heights, "Should have checkpoint from file"
    assert 55795 in heights, "Should have built-in checkpoint"
    
    # Verify the specific mainnet checkpoint
    cp = verifier.get_checkpoint_at_height(55795)
    assert cp is not None
    assert cp.hash == "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938"


@pytest.mark.asyncio
async def test_initialize_checkpoints_backward_compat():
    """Test backward compatibility: old code without chain_id still works."""
    config = CheckpointsConfig(mode="off")
    
    # Old-style call without chain_id should still work (returns None)
    verifier = await initialize_checkpoints(config)
    
    assert verifier is None, "Should get None in off mode without chain_id"
