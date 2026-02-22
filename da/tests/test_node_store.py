"""
Tests for da.node_store — NodeDAStore

Covers:
- blob_id hashing correctness
- atomic write + index update
- put/get/has round-trip
- idempotent put (same data → same id)
- list / pagination
- delete
- gc (older_than_seconds + target_bytes)
- quota enforcement: evict policy
- quota enforcement: reject policy
- config persistence across store reload
- stats
"""

from __future__ import annotations

import hashlib
import os
import time
import tempfile
from pathlib import Path

import pytest

from da.node_store import NodeDAStore, NodeDAConfig, _compute_blob_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_store(tmp_path):
    """Fresh NodeDAStore in a temporary directory."""
    store = NodeDAStore(str(tmp_path / "da_store"))
    # Enable for operations that check .config.enabled
    store.update_config(enabled=True)
    yield store
    store.close()


# ---------------------------------------------------------------------------
# blob_id hashing
# ---------------------------------------------------------------------------


def test_blob_id_is_sha3_256():
    data = b"hello animica"
    expected = hashlib.sha3_256(data).hexdigest()
    assert _compute_blob_id(data) == expected


def test_blob_id_empty_bytes():
    assert _compute_blob_id(b"") == hashlib.sha3_256(b"").hexdigest()


def test_blob_id_deterministic():
    data = b"some bytes"
    assert _compute_blob_id(data) == _compute_blob_id(data)


# ---------------------------------------------------------------------------
# put / get / has round-trip
# ---------------------------------------------------------------------------


def test_put_get_roundtrip(tmp_store):
    data = b"test blob data"
    blob_id, size = tmp_store.put(data)
    assert size == len(data)
    assert blob_id == _compute_blob_id(data)

    retrieved, meta = tmp_store.get(blob_id)
    assert retrieved == data


def test_put_has(tmp_store):
    data = b"existence check"
    blob_id, _ = tmp_store.put(data)
    assert tmp_store.has(blob_id) is True
    assert tmp_store.has("0" * 64) is False


def test_put_idempotent(tmp_store):
    data = b"same data twice"
    id1, sz1 = tmp_store.put(data)
    id2, sz2 = tmp_store.put(data)
    assert id1 == id2
    assert sz1 == sz2
    # Only one blob in store
    items, _ = tmp_store.list_blobs()
    assert len(items) == 1


def test_put_with_metadata(tmp_store):
    data = b"metadata blob"
    blob_id, _ = tmp_store.put(
        data,
        content_type="application/octet-stream",
        owner="anim1test",
        metadata={"tag": "unit-test"},
    )
    _, meta = tmp_store.get(blob_id)
    assert meta.get("content_type") == "application/octet-stream"
    assert meta.get("owner") == "anim1test"
    assert meta.get("tag") == "unit-test"


def test_get_missing_raises(tmp_store):
    with pytest.raises(FileNotFoundError):
        tmp_store.get("a" * 64)


def test_get_verify_integrity(tmp_store):
    data = b"integrity check"
    blob_id, _ = tmp_store.put(data)
    # Normal get with verify should pass
    retrieved, _ = tmp_store.get(blob_id, verify=True)
    assert retrieved == data


def test_atomic_write_creates_file(tmp_store):
    data = b"atomic write test"
    blob_id, _ = tmp_store.put(data)
    # File must exist at the expected sharded path
    expected_path = os.path.join(
        tmp_store.blobs_dir,
        blob_id[:2],
        blob_id[2:4],
        f"{blob_id}.blob",
    )
    assert os.path.exists(expected_path)
    assert open(expected_path, "rb").read() == data


# ---------------------------------------------------------------------------
# list / pagination
# ---------------------------------------------------------------------------


def test_list_empty(tmp_store):
    items, next_cur = tmp_store.list_blobs()
    assert items == []
    assert next_cur is None


def test_list_returns_items(tmp_store):
    for i in range(5):
        tmp_store.put(f"blob {i}".encode())
    items, _ = tmp_store.list_blobs()
    assert len(items) == 5


def test_list_limit(tmp_store):
    for i in range(10):
        tmp_store.put(f"blob {i}".encode())
    items, next_cur = tmp_store.list_blobs(limit=3)
    assert len(items) == 3
    assert next_cur is not None


def test_list_pagination(tmp_store):
    for i in range(5):
        tmp_store.put(f"blob {i:02d}".encode())

    first_page, cursor = tmp_store.list_blobs(limit=3, order="newest")
    assert len(first_page) == 3
    assert cursor is not None

    second_page, cursor2 = tmp_store.list_blobs(limit=3, cursor=cursor, order="newest")
    assert len(second_page) == 2
    assert cursor2 is None


def test_list_lru_order(tmp_store):
    for i in range(3):
        tmp_store.put(f"blob lru {i}".encode())
        time.sleep(0.01)
    items, _ = tmp_store.list_blobs(order="lru")
    # LRU order: least recently accessed first
    assert len(items) == 3


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_removes_blob(tmp_store):
    data = b"to be deleted"
    blob_id, _ = tmp_store.put(data)
    assert tmp_store.has(blob_id) is True
    deleted = tmp_store.delete(blob_id)
    assert deleted is True
    assert tmp_store.has(blob_id) is False


def test_delete_missing_returns_false(tmp_store):
    assert tmp_store.delete("b" * 64) is False


def test_delete_removes_file(tmp_store):
    data = b"file delete test"
    blob_id, _ = tmp_store.put(data)
    path = os.path.join(
        tmp_store.blobs_dir, blob_id[:2], blob_id[2:4], f"{blob_id}.blob"
    )
    assert os.path.exists(path)
    tmp_store.delete(blob_id)
    assert not os.path.exists(path)


# ---------------------------------------------------------------------------
# gc / prune
# ---------------------------------------------------------------------------


def test_gc_older_than(tmp_store):
    data = b"old blob"
    blob_id, _ = tmp_store.put(data)

    # Manually backdate the blob
    tmp_store.db.execute(
        "UPDATE blobs SET created_at=? WHERE blob_id=?",
        (int(time.time()) - 3600, blob_id),
    )
    tmp_store.db.commit()

    freed, removed = tmp_store.gc(older_than_seconds=1800)
    assert removed == 1
    assert freed == len(data)
    assert tmp_store.has(blob_id) is False


def test_gc_target_bytes(tmp_store):
    blobs = [f"blob {i}".encode() for i in range(5)]
    for b in blobs:
        tmp_store.put(b)
        time.sleep(0.01)

    stats_before = tmp_store.stats()
    freed, removed = tmp_store.gc(target_bytes=stats_before["used_bytes"] // 2)
    assert freed > 0
    assert removed > 0


def test_gc_requires_param(tmp_store):
    with pytest.raises(ValueError, match="gc requires"):
        tmp_store.gc()


# ---------------------------------------------------------------------------
# Quota enforcement
# ---------------------------------------------------------------------------


def test_quota_reject(tmp_path):
    store = NodeDAStore(str(tmp_path / "quota_store"))
    store.update_config(enabled=True, max_bytes=20, on_full="reject")
    store.put(b"12345")  # 5 bytes, fine
    with pytest.raises(ValueError, match="quota exceeded"):
        store.put(b"a" * 20)  # would exceed 20-byte limit
    store.close()


def test_quota_evict(tmp_path):
    store = NodeDAStore(str(tmp_path / "quota_evict"))
    store.update_config(enabled=True, max_bytes=30, on_full="evict")
    id1, _ = store.put(b"0123456789")   # 10 bytes
    id2, _ = store.put(b"abcdefghij")   # 10 bytes → used=20
    # Adding 25 bytes would need 15 bytes freed: should evict oldest
    id3, _ = store.put(b"x" * 25)
    # At least one of the first two should be gone
    assert not (store.has(id1) and store.has(id2)), "Expected at least one blob evicted"
    assert store.has(id3)
    store.close()


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


def test_config_persists(tmp_path):
    root = str(tmp_path / "persist_store")
    store1 = NodeDAStore(root)
    store1.update_config(enabled=True, max_bytes=123456789, on_full="reject")
    store1.close()

    store2 = NodeDAStore(root)
    assert store2.config.enabled is True
    assert store2.config.max_bytes == 123456789
    assert store2.config.on_full == "reject"
    store2.close()


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats(tmp_store):
    s = tmp_store.stats()
    assert s["blob_count"] == 0
    assert s["used_bytes"] == 0

    tmp_store.put(b"hello")
    tmp_store.put(b"world!")

    s2 = tmp_store.stats()
    assert s2["blob_count"] == 2
    assert s2["used_bytes"] == len(b"hello") + len(b"world!")


def test_stats_free_bytes_fs(tmp_store):
    s = tmp_store.stats()
    assert s["free_bytes_fs"] >= 0
