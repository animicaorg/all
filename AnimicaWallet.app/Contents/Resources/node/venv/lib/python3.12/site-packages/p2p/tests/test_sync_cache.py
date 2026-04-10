from __future__ import annotations

from pathlib import Path

from p2p.sync.cache_store import SyncCacheConfig, SyncCacheState, SyncCacheStore


def _make_cache(tmp_path: Path) -> SyncCacheStore:
    config = SyncCacheConfig(base_dir=tmp_path)
    return SyncCacheStore(config)


def test_sync_cache_state_roundtrip(tmp_path: Path) -> None:
    store = _make_cache(tmp_path)
    state = SyncCacheState(
        headers=[
            {
                "hash": "aa" * 32,
                "parent_hash": "bb" * 32,
                "height": 12,
                "theta_micro": 10,
                "timestamp": 1234,
            }
        ],
        best_header_hash="aa" * 32,
        block_queue=["cc" * 32],
        block_queue_heights={"cc" * 32: 13},
        peer_penalties={"peer-a": 2},
        last_validated_height=11,
        target_height=100,
        paused=False,
    )
    store.save_state(state)

    reloaded = _make_cache(tmp_path).load_state()
    assert reloaded.best_header_hash == state.best_header_hash
    assert reloaded.block_queue == state.block_queue
    assert reloaded.block_queue_heights == state.block_queue_heights
    assert reloaded.peer_penalties == state.peer_penalties
    assert reloaded.last_validated_height == state.last_validated_height
    assert reloaded.target_height == state.target_height


def test_sync_cache_block_integrity(tmp_path: Path) -> None:
    store = _make_cache(tmp_path)
    block_hash = bytes.fromhex("11" * 32)
    payload = b"animica-block-payload"
    store.put_block(block_hash, payload, height=7, source_peer="peer-1")
    assert store.get_block(block_hash) == payload

    block_path = store.blocks_dir / f"{block_hash.hex()}.bin"
    block_path.write_bytes(b"corrupt")
    assert store.get_block(block_hash) is None
    assert store.cache_entries() == 0
