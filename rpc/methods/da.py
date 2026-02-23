"""Data availability RPC surface — node-side implementation."""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from rpc import errors as rpc_errors
from rpc.methods import method

_log = logging.getLogger("animica.rpc.da")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DA_VERSION = "1.0.0"
_MAX_PUT_BYTES = 32 * 1024 * 1024  # 32 MiB


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_da_dir() -> str:
    base = os.getenv("ANIMICA_DATA_DIR") or os.path.expanduser("~/.animica")
    chain_id = os.getenv("ANIMICA_CHAIN_ID", "1")
    return os.path.join(base, f"chain-{chain_id}", "da")


def _get_store(da_dir: Optional[str] = None):
    """Return (or lazily create) the NodeDAStore for the configured directory."""
    try:
        from da.node_store import get_store
    except ImportError as exc:
        raise rpc_errors.TemporarilyUnavailable(
            f"DA node store not available: {exc}"
        )
    root = da_dir or _default_da_dir()
    return get_store(root)


def _require_store():
    """Return the store only if DA is enabled; raise otherwise."""
    store = _get_store()
    if not store.config.enabled:
        raise rpc_errors.TemporarilyUnavailable(
            "DA is not enabled on this node. "
            "Run da.configure with enabled=true to activate."
        )
    return store


# ---------------------------------------------------------------------------
# RPC methods
# ---------------------------------------------------------------------------


@method("da.status", aliases=("da_status", "da.getStatus", "da_getStatus"),
        desc="Get node-side DA layer status")
def da_status(params=None, *_args, **_kwargs) -> dict:
    """
    Return DA node status.

    Returns a stable schema compatible with Studio/Explorer integration:
    {
      enabled, dir, max_bytes, used_bytes, free_bytes_fs,
      blob_count, last_error, peer_serving,
      allow_remote_get, allow_remote_put,
      version
    }
    """
    try:
        store = _get_store()
        cfg = store.config
        stats = store.stats()
        return {
            "enabled": cfg.enabled,
            "dir": store.root_dir,
            "max_bytes": cfg.max_bytes,
            "used_bytes": stats["used_bytes"],
            "free_bytes_fs": stats["free_bytes_fs"],
            "blob_count": stats["blob_count"],
            "last_error": None,
            "peer_serving": cfg.allow_remote_get,
            "allow_remote_get": cfg.allow_remote_get,
            "allow_remote_put": cfg.allow_remote_put,
            "eviction_policy": cfg.eviction_policy,
            "on_full": cfg.on_full,
            "version": _DA_VERSION,
        }
    except rpc_errors.RpcError:
        raise
    except Exception as exc:
        _log.warning("da.status failed: %s", exc)
        return {
            "enabled": False,
            "dir": _default_da_dir(),
            "max_bytes": 0,
            "used_bytes": 0,
            "free_bytes_fs": 0,
            "blob_count": 0,
            "last_error": str(exc),
            "peer_serving": False,
            "allow_remote_get": False,
            "allow_remote_put": False,
            "eviction_policy": "lru",
            "on_full": "evict",
            "version": _DA_VERSION,
        }




@method("da.getDefaultDir", aliases=("da_getDefaultDir",), desc="Get default node-side DA directory")
def da_get_default_dir(params=None, *_args, **_kwargs) -> dict:
    _ = params
    return {"dir": "/data/da"}


@method("da.getAllowedBaseDirs", aliases=("da_getAllowedBaseDirs",), desc="Get allowed base directories for DA store")
def da_get_allowed_base_dirs(params=None, *_args, **_kwargs) -> dict:
    _ = params
    return {"dirs": ["/data"]}


@method("da.configure", aliases=("da_configure",), desc="Configure node-side DA store")
def da_configure(params=None, **kwargs) -> dict:
    """
    Configure or reconfigure the node-side DA store.

    Accepted parameters (all optional; pass only what you want to change):
      enabled        bool
      dir            str   — absolute path to the store root
      max_bytes      int   — hard storage cap (0 = unlimited)
      eviction_policy str  — "lru" (default; only supported value)
      on_full        str   — "evict" (default) or "reject"
      allow_remote_get bool
      allow_remote_put bool

    Returns the resulting da.status dict.
    """
    # Normalise params: accept positional list, dict, or kwargs
    if isinstance(params, dict):
        kwargs.update(params)
    elif isinstance(params, (list, tuple)) and params:
        if isinstance(params[0], dict):
            kwargs.update(params[0])

    # Validate
    enabled = kwargs.get("enabled")
    da_dir = kwargs.get("dir")
    max_bytes = kwargs.get("max_bytes")
    eviction_policy = kwargs.get("eviction_policy", "lru")
    on_full = kwargs.get("on_full", "evict")
    allow_remote_get = kwargs.get("allow_remote_get")
    allow_remote_put = kwargs.get("allow_remote_put")

    if eviction_policy not in ("lru",):
        raise rpc_errors.InvalidParams(
            f"Invalid eviction_policy: {eviction_policy!r}. Must be 'lru'."
        )
    if on_full not in ("evict", "reject"):
        raise rpc_errors.InvalidParams(
            f"Invalid on_full: {on_full!r}. Must be 'evict' or 'reject'."
        )
    if max_bytes is not None and int(max_bytes) < 0:
        raise rpc_errors.InvalidParams("max_bytes must be >= 0")

    # Resolve directory
    try:
        root = os.path.abspath(da_dir) if da_dir else _default_da_dir()
        Path(root).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        msg = str(exc)
        if "Read-only file system" in msg or "Errno 30" in msg:
            raise rpc_errors.InvalidParams(
                f"Cannot create DA directory: {exc}. Node runs in a container; use a writable path under /data (for example /data/da)."
            )
        raise rpc_errors.InvalidParams(f"Cannot create DA directory: {exc}")

    if not os.access(root, os.W_OK):
        raise rpc_errors.InvalidParams(f"DA directory is not writable: {root}")

    try:
        from da.node_store import get_store, invalidate_store
    except ImportError as exc:
        raise rpc_errors.TemporarilyUnavailable(f"DA node store not available: {exc}")

    # Invalidate cache so we get a fresh store at the new dir if it changed
    invalidate_store(root)
    store = get_store(root)

    update_kwargs: Dict[str, Any] = {"dir": root}
    if enabled is not None:
        update_kwargs["enabled"] = bool(enabled)
    if max_bytes is not None:
        update_kwargs["max_bytes"] = int(max_bytes)
    if eviction_policy:
        update_kwargs["eviction_policy"] = eviction_policy
    if on_full:
        update_kwargs["on_full"] = on_full
    if allow_remote_get is not None:
        update_kwargs["allow_remote_get"] = bool(allow_remote_get)
    if allow_remote_put is not None:
        update_kwargs["allow_remote_put"] = bool(allow_remote_put)

    store.update_config(**update_kwargs)

    # Return current status
    return da_status()


@method("da.put", aliases=("da_put", "da.putBlob", "da_putBlob"),
        desc="Ingest a blob into the node DA store")
def da_put(params=None, **kwargs) -> dict:
    """
    Ingest a blob.

    Params (dict or positional):
      bytes          str  — base64-encoded blob data (required)
      metadata       dict — optional metadata (content_type, owner, tags, ...)

    Returns:
      {blob_id, size_bytes}
    """
    if isinstance(params, dict):
        kwargs.update(params)
    elif isinstance(params, (list, tuple)) and params:
        if isinstance(params[0], dict):
            kwargs.update(params[0])
        else:
            kwargs["bytes"] = params[0]
            if len(params) > 1 and isinstance(params[1], dict):
                kwargs["metadata"] = params[1]

    raw = kwargs.get("bytes") or kwargs.get("data")
    if not raw:
        raise rpc_errors.InvalidParams("Missing required parameter: bytes")

    # Accept base64 or hex
    try:
        if isinstance(raw, str):
            # Try base64 first, then hex
            try:
                blob_bytes = base64.b64decode(raw)
            except Exception:
                blob_bytes = bytes.fromhex(raw.replace("0x", "").replace("0X", ""))
        elif isinstance(raw, bytes):
            blob_bytes = raw
        else:
            raise rpc_errors.InvalidParams(
                f"bytes must be a base64 or hex string, got {type(raw).__name__}"
            )
    except rpc_errors.RpcError:
        raise
    except Exception as exc:
        raise rpc_errors.InvalidParams(f"Cannot decode bytes: {exc}")

    if len(blob_bytes) > _MAX_PUT_BYTES:
        raise rpc_errors.InvalidParams(
            f"Blob too large: {len(blob_bytes)} > {_MAX_PUT_BYTES}"
        )

    metadata = kwargs.get("metadata")
    content_type = None
    owner = None
    if isinstance(metadata, dict):
        content_type = metadata.get("content_type")
        owner = metadata.get("owner")

    try:
        store = _require_store()
        blob_id, size_bytes = store.put(
            blob_bytes,
            content_type=content_type,
            owner=owner,
            metadata=metadata,
        )
    except rpc_errors.RpcError:
        raise
    except ValueError as exc:
        raise rpc_errors.InvalidParams(str(exc))
    except Exception as exc:
        raise rpc_errors.InternalError(f"DA put failed: {exc}")

    return {"blob_id": blob_id, "size_bytes": size_bytes}


@method("da.get", aliases=("da_get", "da.getBlob", "da_getBlob"),
        desc="Retrieve a blob by id from the node DA store")
def da_get(params=None, **kwargs) -> dict:
    """
    Retrieve a blob.

    Params:
      blob_id  str — the blob identifier returned by da.put

    Returns:
      {blob_id, bytes (base64), size_bytes, metadata}
    """
    if isinstance(params, dict):
        kwargs.update(params)
    elif isinstance(params, (list, tuple)) and params:
        kwargs["blob_id"] = params[0]

    blob_id = kwargs.get("blob_id") or kwargs.get("id") or kwargs.get("commitment")
    if not blob_id:
        raise rpc_errors.InvalidParams("Missing required parameter: blob_id")

    try:
        store = _require_store()
        data, meta = store.get(str(blob_id))
    except FileNotFoundError:
        raise rpc_errors.NotFound(f"blob {blob_id}")
    except rpc_errors.RpcError:
        raise
    except Exception as exc:
        raise rpc_errors.InternalError(f"DA get failed: {exc}")

    return {
        "blob_id": blob_id,
        "bytes": base64.b64encode(data).decode("ascii"),
        "size_bytes": len(data),
        "metadata": meta,
    }


@method("da.has", aliases=("da_has",), desc="Check if a blob exists in the node DA store")
def da_has(params=None, **kwargs) -> dict:
    """
    Check blob existence.

    Params:
      blob_id  str

    Returns:
      {blob_id, exists: bool}
    """
    if isinstance(params, dict):
        kwargs.update(params)
    elif isinstance(params, (list, tuple)) and params:
        kwargs["blob_id"] = params[0]

    blob_id = kwargs.get("blob_id") or kwargs.get("id") or kwargs.get("commitment")
    if not blob_id:
        raise rpc_errors.InvalidParams("Missing required parameter: blob_id")

    try:
        store = _require_store()
        exists = store.has(str(blob_id))
    except rpc_errors.RpcError:
        raise
    except Exception as exc:
        raise rpc_errors.InternalError(f"DA has failed: {exc}")

    return {"blob_id": blob_id, "exists": exists}


@method("da.list", aliases=("da_list",), desc="List blobs in the node DA store")
def da_list(params=None, **kwargs) -> dict:
    """
    List stored blobs with pagination.

    Params (all optional):
      limit   int    — max results (default 50, max 1000)
      cursor  str    — opaque pagination cursor from previous call
      order   str    — "newest" (default) or "lru"

    Returns:
      {items: [{blob_id, size_bytes, created_at, last_accessed_at}], next_cursor}
    """
    if isinstance(params, dict):
        kwargs.update(params)
    elif isinstance(params, (list, tuple)) and params and isinstance(params[0], dict):
        kwargs.update(params[0])

    limit = int(kwargs.get("limit", 50))
    cursor = kwargs.get("cursor")
    order = str(kwargs.get("order", "newest"))

    if order not in ("newest", "lru"):
        raise rpc_errors.InvalidParams(
            f"Invalid order: {order!r}. Must be 'newest' or 'lru'."
        )

    try:
        store = _require_store()
        items, next_cursor = store.list_blobs(
            limit=limit, cursor=cursor, order=order
        )
    except rpc_errors.RpcError:
        raise
    except Exception as exc:
        raise rpc_errors.InternalError(f"DA list failed: {exc}")

    return {"items": items, "next_cursor": next_cursor}


@method("da.delete", aliases=("da_delete",), desc="Delete a blob from the node DA store")
def da_delete(params=None, **kwargs) -> dict:
    """
    Delete a blob.

    Params:
      blob_id  str

    Returns:
      {blob_id, deleted: bool}
    """
    if isinstance(params, dict):
        kwargs.update(params)
    elif isinstance(params, (list, tuple)) and params:
        kwargs["blob_id"] = params[0]

    blob_id = kwargs.get("blob_id") or kwargs.get("id")
    if not blob_id:
        raise rpc_errors.InvalidParams("Missing required parameter: blob_id")

    try:
        store = _require_store()
        deleted = store.delete(str(blob_id))
    except rpc_errors.RpcError:
        raise
    except Exception as exc:
        raise rpc_errors.InternalError(f"DA delete failed: {exc}")

    return {"blob_id": blob_id, "deleted": deleted}


@method("da.gc", aliases=("da_gc", "da.prune", "da_prune"),
        desc="Garbage-collect blobs from the node DA store")
def da_gc(params=None, **kwargs) -> dict:
    """
    Garbage-collect (prune) blobs.

    Params (at least one required):
      target_bytes        int — free at least this many bytes via LRU eviction
      older_than_seconds  int — remove blobs older than this many seconds

    Returns:
      {freed_bytes, removed_count}
    """
    if isinstance(params, dict):
        kwargs.update(params)
    elif isinstance(params, (list, tuple)) and params and isinstance(params[0], dict):
        kwargs.update(params[0])

    target_bytes = kwargs.get("target_bytes")
    older_than_seconds = kwargs.get("older_than_seconds")

    if target_bytes is None and older_than_seconds is None:
        raise rpc_errors.InvalidParams(
            "Requires at least one of: target_bytes, older_than_seconds"
        )

    try:
        store = _require_store()
        freed, removed = store.gc(
            target_bytes=int(target_bytes) if target_bytes is not None else None,
            older_than_seconds=int(older_than_seconds) if older_than_seconds is not None else None,
        )
    except rpc_errors.RpcError:
        raise
    except ValueError as exc:
        raise rpc_errors.InvalidParams(str(exc))
    except Exception as exc:
        raise rpc_errors.InternalError(f"DA gc failed: {exc}")

    return {"freed_bytes": freed, "removed_count": removed}


@method("da.getProof", aliases=("da_getProof",), desc="Get DA proof for a blob commitment")
def da_get_proof(*_args, **_kwargs):
    raise rpc_errors.TemporarilyUnavailable("Blob proof not available on this node")


__all__ = [
    "da_status",
    "da_get_default_dir",
    "da_get_allowed_base_dirs",
    "da_configure",
    "da_put",
    "da_get",
    "da_has",
    "da_list",
    "da_delete",
    "da_gc",
    "da_get_proof",
]
