"""Tests for checkpoint loader."""

import json
import pytest
from pathlib import Path

from p2p.checkpoints.config import CheckpointsConfig
from p2p.checkpoints.loader import Checkpoint, CheckpointLoader


def test_checkpoint_creation():
    """Test creating a valid checkpoint."""
    cp = Checkpoint(height=100, hash="0x1234abcd")
    
    assert cp.height == 100
    assert cp.hash == "0x1234abcd"


def test_checkpoint_normalizes_hash():
    """Test that checkpoint normalizes hash to lowercase with 0x prefix."""
    cp = Checkpoint(height=100, hash="1234ABCD")
    
    assert cp.hash == "0x1234abcd"
    
    cp2 = Checkpoint(height=100, hash="0x1234ABCD")
    assert cp2.hash == "0x1234abcd"


def test_checkpoint_validation():
    """Test checkpoint validation."""
    # Negative height should raise
    with pytest.raises(ValueError, match="non-negative"):
        Checkpoint(height=-1, hash="0x1234")
    
    # Non-string hash should raise
    with pytest.raises(ValueError, match="must be a string"):
        Checkpoint(height=100, hash=1234)  # type: ignore


@pytest.mark.asyncio
async def test_loader_disabled_mode():
    """Test loader returns empty list when disabled."""
    config = CheckpointsConfig(mode="off")
    loader = CheckpointLoader(config)
    
    checkpoints = await loader.load_checkpoints()
    
    assert checkpoints == []


@pytest.mark.asyncio
async def test_loader_file_mode(tmp_path):
    """Test loading checkpoints from file."""
    # Create test checkpoint file
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
    loader = CheckpointLoader(config)
    
    checkpoints = await loader.load_checkpoints()
    
    assert len(checkpoints) == 2
    assert checkpoints[0].height == 100
    assert checkpoints[0].hash == "0xabc123"
    assert checkpoints[1].height == 200
    assert checkpoints[1].hash == "0xdef456"


@pytest.mark.asyncio
async def test_loader_file_mode_missing_file():
    """Test loader handles missing file gracefully in non-strict mode."""
    config = CheckpointsConfig(
        mode="file", 
        file_path="/nonexistent/checkpoints.json",
        strict=False
    )
    loader = CheckpointLoader(config)
    
    # Should return empty list and log warning
    checkpoints = await loader.load_checkpoints()
    
    assert checkpoints == []


@pytest.mark.asyncio
async def test_loader_file_mode_missing_file_strict():
    """Test loader raises in strict mode when file is missing."""
    config = CheckpointsConfig(
        mode="file", 
        file_path="/nonexistent/checkpoints.json",
        strict=True
    )
    loader = CheckpointLoader(config)
    
    with pytest.raises(FileNotFoundError):
        await loader.load_checkpoints()


@pytest.mark.asyncio
async def test_loader_parses_list_format(tmp_path):
    """Test parsing checkpoint data as plain list."""
    checkpoint_data = [
        {"height": 100, "hash": "0xabc123"},
        {"height": 200, "hash": "0xdef456"},
    ]
    
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    config = CheckpointsConfig(mode="file", file_path=str(checkpoint_file))
    loader = CheckpointLoader(config)
    
    checkpoints = await loader.load_checkpoints()
    
    assert len(checkpoints) == 2
    assert checkpoints[0].height == 100
    assert checkpoints[1].height == 200


@pytest.mark.asyncio
async def test_loader_sorts_by_height(tmp_path):
    """Test that checkpoints are sorted by height."""
    checkpoint_data = [
        {"height": 300, "hash": "0x333"},
        {"height": 100, "hash": "0x111"},
        {"height": 200, "hash": "0x222"},
    ]
    
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    config = CheckpointsConfig(mode="file", file_path=str(checkpoint_file))
    loader = CheckpointLoader(config)
    
    checkpoints = await loader.load_checkpoints()
    
    assert len(checkpoints) == 3
    assert checkpoints[0].height == 100
    assert checkpoints[1].height == 200
    assert checkpoints[2].height == 300


@pytest.mark.asyncio
async def test_loader_skips_invalid_entries(tmp_path):
    """Test that invalid checkpoint entries are skipped with warnings."""
    checkpoint_data = [
        {"height": 100, "hash": "0xabc123"},
        {"height": "invalid"},  # missing hash
        {"hash": "0xdef456"},  # missing height
        {"height": 200, "hash": "0xdef456"},
    ]
    
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    config = CheckpointsConfig(mode="file", file_path=str(checkpoint_file))
    loader = CheckpointLoader(config)
    
    checkpoints = await loader.load_checkpoints()
    
    # Should have only 2 valid checkpoints
    assert len(checkpoints) == 2
    assert checkpoints[0].height == 100
    assert checkpoints[1].height == 200


@pytest.mark.asyncio
async def test_loader_cache(tmp_path):
    """Test that loader caches checkpoints."""
    checkpoint_data = {"checkpoints": [{"height": 100, "hash": "0xabc123"}]}
    
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    config = CheckpointsConfig(mode="file", file_path=str(checkpoint_file))
    loader = CheckpointLoader(config)
    
    # Load once
    checkpoints1 = await loader.load_checkpoints()
    
    # Modify file
    checkpoint_data["checkpoints"].append({"height": 200, "hash": "0xdef456"})
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    # Load again - should use cache
    checkpoints2 = await loader.load_checkpoints()
    
    assert len(checkpoints1) == 1
    assert len(checkpoints2) == 1  # Still cached
    assert checkpoints1[0].height == checkpoints2[0].height


@pytest.mark.asyncio
async def test_loader_cache_expiry(tmp_path):
    """Test that cache expires after max_age."""
    import time
    
    checkpoint_data = {"checkpoints": [{"height": 100, "hash": "0xabc123"}]}
    
    checkpoint_file = tmp_path / "checkpoints.json"
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    # Very short cache expiry
    config = CheckpointsConfig(
        mode="file", 
        file_path=str(checkpoint_file),
        max_age_seconds=1
    )
    loader = CheckpointLoader(config)
    
    # Load once
    checkpoints1 = await loader.load_checkpoints()
    assert len(checkpoints1) == 1
    
    # Wait for cache to expire
    time.sleep(1.1)
    
    # Modify file
    checkpoint_data["checkpoints"].append({"height": 200, "hash": "0xdef456"})
    with checkpoint_file.open("w") as f:
        json.dump(checkpoint_data, f)
    
    # Load again - cache should be expired
    checkpoints2 = await loader.load_checkpoints()
    
    assert len(checkpoints2) == 2  # New data loaded
