"""Tests for checkpoint loader with built-in checkpoint support."""

import json
import pytest
from pathlib import Path

from p2p.checkpoints.config import CheckpointsConfig
from p2p.checkpoints.loader import CheckpointLoader


@pytest.mark.asyncio
async def test_loader_builtin_mainnet():
    """Test loading built-in checkpoints for mainnet."""
    config = CheckpointsConfig(mode="off")
    loader = CheckpointLoader(config, chain_id=1)
    
    # Load with built-in enabled
    checkpoints = await loader.load_checkpoints(include_builtin=True)
    
    assert len(checkpoints) >= 1, "Should load built-in mainnet checkpoints"
    
    # Find the specific checkpoint
    cp_55795 = next((cp for cp in checkpoints if cp.height == 55795), None)
    assert cp_55795 is not None
    assert cp_55795.hash == "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938"


@pytest.mark.asyncio
async def test_loader_builtin_testnet():
    """Test loading built-in checkpoints for testnet."""
    config = CheckpointsConfig(mode="off")
    loader = CheckpointLoader(config, chain_id=2)
    
    # Load with built-in enabled
    checkpoints = await loader.load_checkpoints(include_builtin=True)
    
    # Testnet has no built-in checkpoints
    assert checkpoints == []


@pytest.mark.asyncio
async def test_loader_no_builtin_when_disabled():
    """Test that built-in checkpoints are not loaded when include_builtin=False."""
    config = CheckpointsConfig(mode="off")
    loader = CheckpointLoader(config, chain_id=1)
    
    # Load without built-in
    checkpoints = await loader.load_checkpoints(include_builtin=False)
    
    assert checkpoints == []


@pytest.mark.asyncio
async def test_loader_no_builtin_without_chain_id():
    """Test that built-in checkpoints are not loaded when chain_id is None."""
    config = CheckpointsConfig(mode="off")
    loader = CheckpointLoader(config, chain_id=None)
    
    # Load with built-in enabled but no chain_id
    checkpoints = await loader.load_checkpoints(include_builtin=True)
    
    assert checkpoints == []


@pytest.mark.asyncio
async def test_loader_merge_file_with_builtin(tmp_path):
    """Test merging file checkpoints with built-in checkpoints."""
    # Create test checkpoint file with different heights
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
    loader = CheckpointLoader(config, chain_id=1)
    
    # Load with built-in enabled
    checkpoints = await loader.load_checkpoints(include_builtin=True)
    
    # Should have both file checkpoints and built-in mainnet checkpoint
    assert len(checkpoints) >= 3
    
    # Check that both sources are present
    heights = {cp.height for cp in checkpoints}
    assert 1000 in heights, "Should have checkpoint from file"
    assert 2000 in heights, "Should have checkpoint from file"
    assert 55795 in heights, "Should have built-in mainnet checkpoint"


@pytest.mark.asyncio
async def test_loader_builtin_takes_precedence(tmp_path):
    """Test that built-in checkpoints take precedence over file checkpoints."""
    # Create test checkpoint file with conflicting height
    checkpoint_data = {
        "checkpoints": [
            {"height": 55795, "hash": "0x1111111111111111111111111111111111111111111111111111111111111111"},  # Wrong hash at same height
            {"height": 2000, "hash": "0xdef456"},
        ]
    }
    
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    config = CheckpointsConfig(mode="file", file_path=str(checkpoint_file))
    loader = CheckpointLoader(config, chain_id=1)
    
    # Load with built-in enabled
    checkpoints = await loader.load_checkpoints(include_builtin=True)
    
    # Find checkpoint at height 55795
    cp_55795 = next((cp for cp in checkpoints if cp.height == 55795), None)
    
    assert cp_55795 is not None
    # Should use built-in hash, not the wrong one from file
    assert cp_55795.hash == "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938"
    # Verify it's not the wrong hash from file
    assert cp_55795.hash != "0x1111111111111111111111111111111111111111111111111111111111111111"


@pytest.mark.asyncio
async def test_loader_builtin_fallback_on_error(tmp_path):
    """Test that built-in checkpoints are returned when external source fails."""
    # Configure with non-existent file in non-strict mode
    config = CheckpointsConfig(
        mode="file",
        file_path="/nonexistent/checkpoints.json",
        strict=False
    )
    loader = CheckpointLoader(config, chain_id=1)
    
    # Load with built-in enabled
    checkpoints = await loader.load_checkpoints(include_builtin=True)
    
    # Should still get built-in checkpoints even though file load failed
    assert len(checkpoints) >= 1
    
    cp_55795 = next((cp for cp in checkpoints if cp.height == 55795), None)
    assert cp_55795 is not None


@pytest.mark.asyncio
async def test_loader_builtin_sorted():
    """Test that merged checkpoints are sorted by height."""
    # Create test checkpoint file
    checkpoint_data = {
        "checkpoints": [
            {"height": 100000, "hash": "0xhigh"},  # Higher than built-in
            {"height": 1000, "hash": "0xlow"},     # Lower than built-in
        ]
    }
    
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(checkpoint_data, f)
        checkpoint_file = f.name
    
    try:
        config = CheckpointsConfig(mode="file", file_path=checkpoint_file)
        loader = CheckpointLoader(config, chain_id=1)
        
        checkpoints = await loader.load_checkpoints(include_builtin=True)
        
        # Verify sorted order
        for i in range(len(checkpoints) - 1):
            assert checkpoints[i].height < checkpoints[i + 1].height
    
    finally:
        Path(checkpoint_file).unlink()
