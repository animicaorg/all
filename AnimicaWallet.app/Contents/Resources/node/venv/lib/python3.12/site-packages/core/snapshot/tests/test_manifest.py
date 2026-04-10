from core.snapshot.manifest import SnapshotManifest, select_best_manifest


def test_select_best_manifest_prefers_height_and_time():
    older = SnapshotManifest(
        schema_version=2,
        chain_id=1,
        network="mainnet",
        created_at="2024-01-01T00:00:00Z",
        head_height=100,
        head_hash="0xabc",
        total_size=0,
        chunks=[],
    )
    newer = SnapshotManifest(
        schema_version=2,
        chain_id=1,
        network="mainnet",
        created_at="2024-01-02T00:00:00Z",
        head_height=100,
        head_hash="0xdef",
        total_size=0,
        chunks=[],
    )
    higher = SnapshotManifest(
        schema_version=2,
        chain_id=1,
        network="mainnet",
        created_at="2024-01-01T00:00:00Z",
        head_height=200,
        head_hash="0x123",
        total_size=0,
        chunks=[],
    )

    assert select_best_manifest([older, newer]).head_hash == "0xdef"
    assert select_best_manifest([newer, higher]).head_hash == "0x123"
