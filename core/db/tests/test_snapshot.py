"""
Tests for snapshot export/import functionality.
"""

import tempfile
from pathlib import Path

import pytest


def test_snapshot_manifest_creation():
    """Test that SnapshotManifest can be created with basic fields."""
    from core.db.snapshot import SnapshotManifest

    manifest = SnapshotManifest(
        version=1,
        chain_id=1,
        checkpoint_height=1000,
        checkpoint_hash="0x1234",
        timestamp=1234567890,
        blocks_count=1000,
        headers_count=1000,
        accounts_count=100,
        storage_keys_count=500,
        code_contracts_count=10,
    )

    assert manifest.version == 1
    assert manifest.chain_id == 1
    assert manifest.checkpoint_height == 1000
    assert manifest.checkpoint_hash == "0x1234"
    assert manifest.blocks_count == 1000


def test_hex_encoding():
    """Test hex encoding utilities."""
    from core.db.snapshot import _hex, _unhex

    # Test hex encoding
    data = b"\x12\x34\x56\x78"
    encoded = _hex(data)
    assert encoded == "0x12345678"

    # Test hex decoding
    decoded = _unhex(encoded)
    assert decoded == data

    # Test without 0x prefix
    decoded2 = _unhex("12345678")
    assert decoded2 == data


def test_verify_snapshot_nonexistent():
    """Test verifying a non-existent snapshot."""
    from core.db.snapshot import verify_snapshot

    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = Path(tmpdir) / "nonexistent"
        is_valid, errors = verify_snapshot(snapshot_dir)

        assert not is_valid
        assert len(errors) > 0
        assert any("not found" in err.lower() for err in errors)


def test_verify_snapshot_no_manifest():
    """Test verifying a snapshot directory without manifest."""
    from core.db.snapshot import verify_snapshot

    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_dir = Path(tmpdir)
        # Create directory but no manifest
        snapshot_dir.mkdir(exist_ok=True)

        is_valid, errors = verify_snapshot(snapshot_dir)

        assert not is_valid
        assert len(errors) > 0
        assert any("manifest" in err.lower() for err in errors)


def test_snapshot_version_constant():
    """Test that snapshot version constant is defined."""
    from core.db.snapshot import SNAPSHOT_VERSION

    assert isinstance(SNAPSHOT_VERSION, int)
    assert SNAPSHOT_VERSION >= 1


def test_snapshot_default_chunk_size():
    """Test that default chunk size is defined."""
    from core.db.snapshot import DEFAULT_CHUNK_SIZE

    assert isinstance(DEFAULT_CHUNK_SIZE, int)
    assert DEFAULT_CHUNK_SIZE > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
