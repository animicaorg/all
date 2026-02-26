"""
Tests for rpc.methods.da — node-side DA RPC methods.

Uses unittest.mock to patch NodeDAStore so no real filesystem I/O occurs.
"""

from __future__ import annotations

import base64
import hashlib
import tempfile
import os
from unittest.mock import MagicMock, patch
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _make_store_mock(enabled=True, **overrides):
    """Build a mock NodeDAStore with sensible defaults."""
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.max_bytes = 10 * 1024 ** 3
    cfg.allow_remote_get = True
    cfg.allow_remote_put = True  # default True so put tests pass; set to False to test policy
    cfg.eviction_policy = "lru"
    cfg.on_full = "evict"

    store = MagicMock()
    store.config = cfg
    store.root_dir = "/tmp/da_test_store"
    store.stats.return_value = {
        "blob_count": 0,
        "used_bytes": 0,
        "free_bytes_fs": 1_000_000_000,
        "max_bytes": 10 * 1024 ** 3,
    }
    for k, v in overrides.items():
        setattr(store, k, v)
    return store


# ---------------------------------------------------------------------------
# da.status
# ---------------------------------------------------------------------------


def test_da_status_basic():
    from rpc.methods.da import da_status

    store = _make_store_mock(enabled=True)
    with patch("rpc.methods.da._get_store", return_value=store), \
         patch("rpc.methods.da.os.access", return_value=True):
        result = da_status()

    assert result["enabled"] is True
    assert result["blob_count"] == 0
    assert result["version"] == "1.0.0"


def test_da_status_disabled():
    from rpc.methods.da import da_status

    store = _make_store_mock(enabled=False)
    with patch("rpc.methods.da._get_store", return_value=store):
        result = da_status()

    assert result["enabled"] is False


def test_da_status_exception_returns_error_dict():
    from rpc.methods.da import da_status

    with patch("rpc.methods.da._get_store", side_effect=RuntimeError("oops")):
        result = da_status()

    assert result["enabled"] is False
    assert result["last_error"] == "oops"


# ---------------------------------------------------------------------------
# da.configure
# ---------------------------------------------------------------------------


def test_da_configure_basic(tmp_path, monkeypatch):
    from rpc.methods.da import da_configure

    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.setenv("ANIMICA_DA_ALLOWED_BASE_DIRS", str(tmp_path))

    target = tmp_path / "basic-da"
    result = da_configure({"enabled": True, "dir": str(target), "max_bytes": 1000})
    assert isinstance(result, dict)
    assert result["enabled"] is True
    assert result["ok"] is True


def test_da_configure_invalid_on_full():
    from rpc.methods.da import da_configure
    from rpc.errors import InvalidParams

    with pytest.raises(InvalidParams, match="on_full"):
        da_configure({"on_full": "unknown"})


def test_da_configure_invalid_eviction_policy():
    from rpc.methods.da import da_configure
    from rpc.errors import InvalidParams

    with pytest.raises(InvalidParams, match="eviction_policy"):
        da_configure({"eviction_policy": "fifo"})


def test_da_configure_negative_max_bytes():
    from rpc.methods.da import da_configure
    from rpc.errors import InvalidParams

    with pytest.raises(InvalidParams, match="max_bytes"):
        da_configure({"max_bytes": -1})


# ---------------------------------------------------------------------------
# da.put
# ---------------------------------------------------------------------------


def test_da_put_base64():
    from rpc.methods.da import da_put

    data = b"hello da store"
    b64 = base64.b64encode(data).decode()
    expected_id = hashlib.sha3_256(data).hexdigest()

    store = _make_store_mock()
    store.put.return_value = (expected_id, len(data))

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_put({"bytes": b64})

    assert result["blob_id"] == expected_id
    assert result["size_bytes"] == len(data)


def test_da_put_hex_encoded():
    from rpc.methods.da import da_put

    data = b"\xde\xad\xbe\xef"
    hex_str = data.hex()
    expected_id = hashlib.sha3_256(data).hexdigest()

    store = _make_store_mock()
    store.put.return_value = (expected_id, len(data))

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_put({"bytes": hex_str})

    assert result["blob_id"] == expected_id


def test_da_put_missing_bytes():
    from rpc.methods.da import da_put
    from rpc.errors import InvalidParams

    with pytest.raises(InvalidParams, match="bytes"):
        da_put({})


def test_da_put_disabled_store():
    from rpc.methods.da import da_put
    from rpc.errors import TemporarilyUnavailable

    store = _make_store_mock(enabled=False)
    with patch("rpc.methods.da._get_store", return_value=store), \
         patch("rpc.methods.da._require_store",
               side_effect=TemporarilyUnavailable("DA not enabled")):
        with pytest.raises(TemporarilyUnavailable):
            da_put({"bytes": base64.b64encode(b"x").decode()})


def test_da_put_too_large():
    from rpc.methods.da import da_put, _MAX_PUT_BYTES
    from rpc.errors import InvalidParams

    store = _make_store_mock()
    # Generate a large base64 payload that exceeds the limit
    large_b64 = base64.b64encode(b"x" * (_MAX_PUT_BYTES + 1)).decode()
    with patch("rpc.methods.da._require_store", return_value=store):
        with pytest.raises(InvalidParams, match="too large"):
            da_put({"bytes": large_b64})


def test_da_get_ingest_dir_defaults_under_chain_root(tmp_path):
    from rpc.methods.da import da_get_ingest_dir

    store = _make_store_mock()
    store.root_dir = str(tmp_path / "chain-1" / "da")

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_get_ingest_dir({})

    assert result["dir"].endswith("chain-1/da_ingest")
    assert result["pending_dir"].endswith("chain-1/da_ingest/pending")


def test_da_get_data_root(tmp_path):
    from rpc.methods.da import da_get_data_root

    store = _make_store_mock()
    store.root_dir = str(tmp_path / "chain-1" / "da")

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_get_data_root({})

    assert result["node_chain_dir"].endswith("chain-1/da")
    assert result["data_root"].endswith(str(tmp_path))


def test_da_stat_path(tmp_path):
    from rpc.methods.da import da_stat_path

    blob = tmp_path / "x.blob"
    blob.write_bytes(b"ok")
    out = da_stat_path({"path": str(blob)})
    assert out["exists"] is True
    assert out["is_file"] is True


def test_da_ingest_local_reads_file_and_ingests(tmp_path):
    from rpc.methods.da import da_ingest_local

    ingest_dir = tmp_path / "chain-1" / "da_ingest"
    pending = ingest_dir / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    blob = pending / "abc.blob"
    blob.write_bytes(b"hello")

    store = _make_store_mock()
    store.root_dir = str(tmp_path / "chain-1" / "da")
    store.put.return_value = ("c" * 64, 5)

    with patch("rpc.methods.da._require_store", return_value=store), \
         patch("rpc.methods.da._resolve_ingest_dir", return_value=str(ingest_dir)):
        result = da_ingest_local({"path": str(blob), "namespace": 0})

    assert result["blob_id"] == "c" * 64
    assert result["ingested"] is True


def test_da_ingest_local_rejects_path_outside_ingest_dir(tmp_path):
    from rpc.methods.da import da_ingest_local
    from rpc.errors import RpcError

    ingest_dir = tmp_path / "chain-1" / "da_ingest"
    outside = tmp_path / "other" / "x.blob"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"x")

    store = _make_store_mock()
    store.root_dir = str(tmp_path / "chain-1" / "da")

    with patch("rpc.methods.da._require_store", return_value=store), \
         patch("rpc.methods.da._resolve_ingest_dir", return_value=str(ingest_dir)):
        with pytest.raises(RpcError) as exc:
            da_ingest_local({"path": str(outside), "namespace": 0})
    assert exc.value.code == -32005


def test_da_ingest_local_not_found_has_diagnostics(tmp_path):
    from rpc.methods.da import da_ingest_local
    from rpc.errors import NotFound

    ingest_dir = tmp_path / "chain-1" / "da_ingest"
    (ingest_dir / "pending").mkdir(parents=True, exist_ok=True)
    (ingest_dir / "pending" / "other.blob").write_bytes(b"x")
    missing = ingest_dir / "pending" / "missing.blob"

    store = _make_store_mock()
    store.root_dir = str(tmp_path / "chain-1" / "da")

    with patch("rpc.methods.da._require_store", return_value=store), \
         patch("rpc.methods.da._resolve_ingest_dir", return_value=str(ingest_dir)):
        with pytest.raises(NotFound) as exc:
            da_ingest_local({"path": str(missing), "namespace": 0})

    msg = str(exc.value)
    assert "ingest file not found" in msg
    assert "pending_examples" in msg


# ---------------------------------------------------------------------------
# da.get
# ---------------------------------------------------------------------------


def test_da_get_returns_base64():
    from rpc.methods.da import da_get

    data = b"retrieved data"
    b64 = base64.b64encode(data).decode()
    blob_id = hashlib.sha3_256(data).hexdigest()

    store = _make_store_mock()
    store.get.return_value = (data, {})

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_get({"blob_id": blob_id})

    assert result["blob_id"] == blob_id
    assert result["bytes"] == b64
    assert result["size_bytes"] == len(data)


def test_da_get_not_found():
    from rpc.methods.da import da_get
    from rpc.errors import NotFound

    store = _make_store_mock()
    store.get.side_effect = FileNotFoundError("missing")

    with patch("rpc.methods.da._require_store", return_value=store):
        with pytest.raises(NotFound):
            da_get({"blob_id": "a" * 64})


def test_da_get_missing_blob_id():
    from rpc.methods.da import da_get
    from rpc.errors import InvalidParams

    store = _make_store_mock()
    with patch("rpc.methods.da._require_store", return_value=store):
        with pytest.raises(InvalidParams, match="blob_id"):
            da_get({})


# ---------------------------------------------------------------------------
# da.has
# ---------------------------------------------------------------------------


def test_da_has_present():
    from rpc.methods.da import da_has

    store = _make_store_mock()
    store.has.return_value = True

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_has({"blob_id": "a" * 64})

    assert result["exists"] is True


def test_da_has_absent():
    from rpc.methods.da import da_has

    store = _make_store_mock()
    store.has.return_value = False

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_has({"blob_id": "b" * 64})

    assert result["exists"] is False


def test_da_has_missing_id():
    from rpc.methods.da import da_has
    from rpc.errors import InvalidParams

    store = _make_store_mock()
    with patch("rpc.methods.da._require_store", return_value=store):
        with pytest.raises(InvalidParams, match="blob_id"):
            da_has({})


# ---------------------------------------------------------------------------
# da.list
# ---------------------------------------------------------------------------


def test_da_list_empty():
    from rpc.methods.da import da_list

    store = _make_store_mock()
    store.list_blobs.return_value = ([], None)

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_list({})

    assert result["items"] == []
    assert result["next_cursor"] is None


def test_da_list_with_items():
    from rpc.methods.da import da_list

    items = [
        {"blob_id": "a" * 64, "size_bytes": 5, "created_at": 1000, "last_accessed_at": 1000}
    ]
    store = _make_store_mock()
    store.list_blobs.return_value = (items, None)

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_list({"limit": 10, "order": "newest"})

    assert len(result["items"]) == 1


def test_da_list_invalid_order():
    from rpc.methods.da import da_list
    from rpc.errors import InvalidParams

    store = _make_store_mock()
    with patch("rpc.methods.da._require_store", return_value=store):
        with pytest.raises(InvalidParams, match="order"):
            da_list({"order": "random"})


# ---------------------------------------------------------------------------
# da.delete
# ---------------------------------------------------------------------------


def test_da_delete_existing():
    from rpc.methods.da import da_delete

    store = _make_store_mock()
    store.delete.return_value = True
    blob_id = "c" * 64

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_delete({"blob_id": blob_id})

    assert result["deleted"] is True
    assert result["blob_id"] == blob_id


def test_da_delete_missing():
    from rpc.methods.da import da_delete

    store = _make_store_mock()
    store.delete.return_value = False

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_delete({"blob_id": "d" * 64})

    assert result["deleted"] is False


def test_da_delete_missing_id():
    from rpc.methods.da import da_delete
    from rpc.errors import InvalidParams

    store = _make_store_mock()
    with patch("rpc.methods.da._require_store", return_value=store):
        with pytest.raises(InvalidParams, match="blob_id"):
            da_delete({})


# ---------------------------------------------------------------------------
# da.gc
# ---------------------------------------------------------------------------


def test_da_gc_target_bytes():
    from rpc.methods.da import da_gc

    store = _make_store_mock()
    store.gc.return_value = (1000, 3)

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_gc({"target_bytes": 1000})

    assert result["freed_bytes"] == 1000
    assert result["removed_count"] == 3


def test_da_gc_older_than():
    from rpc.methods.da import da_gc

    store = _make_store_mock()
    store.gc.return_value = (500, 1)

    with patch("rpc.methods.da._require_store", return_value=store):
        result = da_gc({"older_than_seconds": 3600})

    assert result["freed_bytes"] == 500


def test_da_gc_no_params():
    from rpc.methods.da import da_gc
    from rpc.errors import InvalidParams

    store = _make_store_mock()
    with patch("rpc.methods.da._require_store", return_value=store):
        with pytest.raises(InvalidParams, match="target_bytes"):
            da_gc({})


def test_da_get_default_dir(tmp_path, monkeypatch):
    from rpc.methods.da import da_get_default_dir

    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    out = da_get_default_dir()
    assert out["dir"] == str(tmp_path / "chain-1" / "da")


def test_da_get_allowed_base_dirs(tmp_path, monkeypatch):
    from rpc.methods.da import da_get_allowed_base_dirs

    monkeypatch.setenv("ANIMICA_DA_ALLOWED_BASE_DIRS", "")
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    out = da_get_allowed_base_dirs()
    assert out["dirs"] == [str(tmp_path)]


def test_da_configure_accepts_object_and_enables_with_status(tmp_path, monkeypatch):
    from da.node_store import invalidate_store
    from rpc.methods.da import da_configure, da_status

    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.setenv("ANIMICA_DA_ALLOWED_BASE_DIRS", str(tmp_path))

    target = tmp_path / "da"
    before = da_status({"dir": str(target)})
    assert before["ok"] is False
    assert before["enabled"] is False

    out = da_configure({"enabled": True, "dir": str(target), "max_bytes": 1024 * 1024})
    assert out["enabled"] is True
    assert out["ok"] is True
    assert out["writable"] is True

    after = da_status({"dir": str(target)})
    assert after["ok"] is True
    assert after["enabled"] is True
    assert after["writable"] is True

    # emulate restart: clear cached stores and read persisted config/status again
    invalidate_store(str(target))
    restarted = da_status()
    assert restarted["ok"] is True
    assert restarted["enabled"] is True


def test_da_configure_accepts_positional_params(tmp_path, monkeypatch):
    from rpc.methods.da import da_configure

    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.setenv("ANIMICA_DA_ALLOWED_BASE_DIRS", str(tmp_path))

    target = tmp_path / "da-pos"
    out = da_configure([True, str(target), 2048])
    assert out["enabled"] is True
    assert out["ok"] is True


def test_da_configure_enabled_requires_dir_and_max_bytes(tmp_path, monkeypatch):
    from rpc.methods.da import da_configure
    from rpc.errors import InvalidParams

    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")

    with pytest.raises(InvalidParams, match="dir"):
        da_configure({"enabled": True, "max_bytes": 1000})

    with pytest.raises(InvalidParams, match="max_bytes"):
        da_configure({"enabled": True, "dir": str(tmp_path / "da")})


def test_da_configure_accepts_single_object_in_positional_list(tmp_path, monkeypatch):
    from rpc.methods.da import da_configure

    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.setenv("ANIMICA_DA_ALLOWED_BASE_DIRS", str(tmp_path))

    target = tmp_path / "da-pos-object"
    out = da_configure([{"enabled": True, "dir": str(target), "max_bytes": 4096}])
    assert out["enabled"] is True
    assert out["ok"] is True


def test_da_configure_missing_enabled_returns_received_keys(tmp_path, monkeypatch):
    from rpc.methods.da import da_configure
    from rpc.errors import InvalidParams

    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.setenv("ANIMICA_DA_ALLOWED_BASE_DIRS", str(tmp_path))

    with pytest.raises(InvalidParams) as excinfo:
        da_configure({"dir": str(tmp_path / "da"), "max_bytes": 1234})

    err = excinfo.value
    assert err.data.get("reason") == "missing_enabled"
    assert "dir" in err.data.get("received_keys", [])
    assert "max_bytes" in err.data.get("received_keys", [])


def test_da_ingest_local_remote_permission_denied(tmp_path, monkeypatch):
    from rpc.methods.da import da_ingest_local
    from rpc.errors import RpcError

    ingest_dir = tmp_path / "chain-1" / "da_ingest"
    pending = ingest_dir / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    blob = pending / "x.blob"
    blob.write_bytes(b"x")

    store = _make_store_mock()
    store.root_dir = str(tmp_path / "chain-1" / "da")

    monkeypatch.setattr("rpc.methods.da._is_local_rpc_request", lambda: False)
    monkeypatch.setattr("rpc.methods.da._ingest_local_guard_enabled", lambda: True)

    with patch("rpc.methods.da._require_store", return_value=store), \
         patch("rpc.methods.da._resolve_ingest_dir", return_value=str(ingest_dir)):
        with pytest.raises(RpcError) as exc:
            da_ingest_local({"path": str(blob), "namespace": 0})

    assert exc.value.code == -32006
