"""
Tests for snapshot export/import functionality.
"""

import json
import tempfile
from pathlib import Path

import pytest


def test_snapshot_manifest_creation():
    """Test that SnapshotManifest can be created with basic fields."""
    from core.db.snapshot import SnapshotManifest

    manifest = SnapshotManifest(
        version=1,
        chain_id=1,
        network="testnet",
        checkpoint_height=1000,
        checkpoint_hash="0x1234",
        timestamp=1234567890,
        created_at="2024-01-01T00:00:00Z",
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


def test_import_snapshot_rejects_corrupted_chunk(tmp_path: Path) -> None:
    from core.db.block_db import BlockDB
    from core.db.snapshot import import_snapshot
    from core.db.sqlite import SQLiteKV
    from core.db.state_db import StateDB

    kv = SQLiteKV(str(tmp_path / "snapshot.db"))
    block_db = BlockDB(kv)
    state_db = StateDB(kv)

    snapshot_dir = tmp_path / "snapshot_corrupt"
    snapshot_dir.mkdir()
    chunk_path = snapshot_dir / "blocks-00000.cbor.gz"
    chunk_path.write_bytes(b"corrupted-data")

    manifest = {
        "version": 2,
        "chain_id": 1,
        "network": "devnet",
        "checkpoint_height": 0,
        "checkpoint_hash": "0x" + "00" * 32,
        "timestamp": 0,
        "created_at": "1970-01-01T00:00:00Z",
        "blocks_count": 0,
        "headers_count": 0,
        "accounts_count": 0,
        "storage_keys_count": 0,
        "code_contracts_count": 0,
        "compressed": True,
        "chunks": [
            {
                "name": chunk_path.name,
                "type": "blocks",
                "size": chunk_path.stat().st_size,
                "sha256": "0x" + "11" * 32,
                "index": 0,
            }
        ],
    }
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        import_snapshot(
            block_db=block_db,
            state_db=state_db,
            snapshot_dir=snapshot_dir,
            verify_hashes=True,
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
