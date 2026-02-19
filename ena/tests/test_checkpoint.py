"""
Tests for ENA checkpoint publishing.
"""

import pytest
from ena.checkpoint import (
    CHECKPOINT_INTERVAL_BLOCKS,
    should_publish_checkpoint,
    compute_checkpoint_version,
    create_checkpoint_manifest,
    serialize_manifest,
    verify_checkpoint_manifest,
)


def test_checkpoint_interval():
    """Test checkpoint interval constant."""
    assert CHECKPOINT_INTERVAL_BLOCKS == 10_000


def test_should_publish_checkpoint():
    """Test checkpoint trigger logic."""
    # Heights that should trigger
    assert should_publish_checkpoint(10_000) is True
    assert should_publish_checkpoint(20_000) is True
    assert should_publish_checkpoint(100_000) is True
    
    # Heights that should not trigger
    assert should_publish_checkpoint(0) is False
    assert should_publish_checkpoint(1) is False
    assert should_publish_checkpoint(9_999) is False
    assert should_publish_checkpoint(10_001) is False
    assert should_publish_checkpoint(15_000) is False


def test_compute_checkpoint_version():
    """Test deterministic version computation."""
    # Default version (0.9.0)
    assert compute_checkpoint_version(10_000) == "ena-v0.9.0-h10000"
    assert compute_checkpoint_version(20_000) == "ena-v0.9.0-h20000"
    
    # Custom version
    assert compute_checkpoint_version(10_000, major=1, minor=0, patch=0) == "ena-v1.0.0-h10000"
    assert compute_checkpoint_version(50_000, major=2, minor=1, patch=5) == "ena-v2.1.5-h50000"


def test_create_checkpoint_manifest():
    """Test checkpoint manifest creation."""
    manifest = create_checkpoint_manifest(
        height=10_000,
        block_hash="0xabc123",
        chain_id=1,
    )
    
    # Check basic fields
    assert manifest.version == "ena-v0.9.0-h10000"
    assert manifest.height == 10_000
    assert manifest.block_hash == "0xabc123"
    assert manifest.chain_id == 1
    assert manifest.created_at > 0
    
    # Check default values
    assert manifest.training_runs == []
    assert manifest.datasets == []
    assert manifest.evals == []
    assert manifest.weights["format"] == "safetensors"
    assert manifest.signatures == []


def test_create_checkpoint_manifest_with_data():
    """Test checkpoint manifest with training data."""
    training_runs = [
        {"job_id": "job1", "status": "completed", "credits": 1000}
    ]
    datasets = [
        {"name": "dataset1", "source": "https://example.com", "license": "MIT"}
    ]
    evals = [
        {"metric": "perplexity", "value": 3.14}
    ]
    
    manifest = create_checkpoint_manifest(
        height=10_000,
        block_hash="0xabc123",
        chain_id=1,
        training_runs=training_runs,
        datasets=datasets,
        evals=evals,
    )
    
    assert manifest.training_runs == training_runs
    assert manifest.datasets == datasets
    assert manifest.evals == evals


def test_serialize_manifest():
    """Test manifest serialization."""
    manifest = create_checkpoint_manifest(
        height=10_000,
        block_hash="0xabc123",
        chain_id=1,
    )
    
    # Serialize
    data = serialize_manifest(manifest)
    
    # Check it's bytes
    assert isinstance(data, bytes)
    
    # Check it's valid JSON
    import json
    manifest_dict = json.loads(data.decode("utf-8"))
    
    # Check fields
    assert manifest_dict["version"] == "ena-v0.9.0-h10000"
    assert manifest_dict["height"] == 10_000
    assert manifest_dict["block_hash"] == "0xabc123"


def test_verify_checkpoint_manifest():
    """Test manifest verification."""
    manifest = create_checkpoint_manifest(
        height=10_000,
        block_hash="0xabc123",
        chain_id=1,
    )
    
    # Valid manifest
    is_valid, error = verify_checkpoint_manifest(manifest)
    assert is_valid is True
    assert error is None
    
    # Valid with height check
    is_valid, error = verify_checkpoint_manifest(manifest, expected_height=10_000)
    assert is_valid is True
    
    # Valid with chain ID check
    is_valid, error = verify_checkpoint_manifest(manifest, expected_chain_id=1)
    assert is_valid is True
    
    # Invalid height
    is_valid, error = verify_checkpoint_manifest(manifest, expected_height=20_000)
    assert is_valid is False
    assert "Height mismatch" in error
    
    # Invalid chain ID
    is_valid, error = verify_checkpoint_manifest(manifest, expected_chain_id=2)
    assert is_valid is False
    assert "Chain ID mismatch" in error


def test_verify_checkpoint_manifest_invalid_version():
    """Test manifest verification with invalid version."""
    manifest = create_checkpoint_manifest(
        height=10_000,
        block_hash="0xabc123",
        chain_id=1,
    )
    
    # Corrupt version format
    manifest.version = "invalid-version"
    is_valid, error = verify_checkpoint_manifest(manifest)
    assert is_valid is False
    assert "Invalid version format" in error
    
    # Version doesn't match height
    manifest.version = "ena-v0.9.0-h20000"
    is_valid, error = verify_checkpoint_manifest(manifest)
    assert is_valid is False
    assert "Version/height mismatch" in error
