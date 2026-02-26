"""Data availability RPC surface — node-side implementation."""

from __future__ import annotations

import base64
import json
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


def _global_da_config_path() -> str:
    return os.path.join(_default_da_dir(), "da_config.json")


def _load_persisted_da_config() -> dict[str, Any]:
    path = _global_da_config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
            return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _persist_da_config(cfg: dict[str, Any]) -> None:
    path = _global_da_config_path()
    Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _resolve_store_dir(da_dir: Optional[str] = None) -> str:
    if da_dir:
        return os.path.abspath(da_dir)
    persisted = _load_persisted_da_config()
    persisted_dir = persisted.get("dir") if isinstance(persisted, dict) else None
    if isinstance(persisted_dir, str) and persisted_dir.strip():
        return os.path.abspath(persisted_dir)
    return _default_da_dir()




def _is_container_runtime() -> bool:
    if os.path.exists('/.dockerenv'):
        return True
    return bool(os.getenv('KUBERNETES_SERVICE_HOST') or os.getenv('ANIMICA_CONTAINERIZED', '').strip().lower() in {'1','true','yes','on'})


def _default_allowed_base_dir() -> str:
    env_base = os.getenv('ANIMICA_DATA_DIR')
    if env_base and env_base.strip():
        return os.path.abspath(os.path.expanduser(env_base.strip()))
    if _is_container_runtime():
        return '/data'
    return os.path.abspath(os.path.expanduser('~/.animica'))
def _allowed_base_dirs() -> list[str]:
    raw = os.getenv("ANIMICA_DA_ALLOWED_BASE_DIRS", _default_allowed_base_dir())
    out: list[str] = []
    for entry in raw.split(":"):
        cleaned = entry.strip()
        if cleaned:
            out.append(os.path.abspath(cleaned))
    return out or [_default_allowed_base_dir()]


def _is_allowed_dir(candidate: str, allowed_dirs: list[str]) -> bool:
    normalized = os.path.abspath(candidate)
    for base in allowed_dirs:
        b = os.path.abspath(str(base))
        if normalized == b or normalized.startswith(f"{b}{os.sep}"):
            return True
    return False


def _get_store(da_dir: Optional[str] = None):
    """Return (or lazily create) the NodeDAStore for the configured directory."""
    try:
        from da.node_store import get_store
    except ImportError as exc:
        raise rpc_errors.TemporarilyUnavailable(
            f"DA node store not available: {exc}"
        )
    root = _resolve_store_dir(da_dir)
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


def _require_remote_put_allowed(store) -> None:
    """Raise a structured POLICY_BLOCKED error if allow_remote_put=false."""
    if not store.config.allow_remote_put:
        raise rpc_errors.AccessDenied(
            "DA remote blob upload is blocked by policy (allow_remote_put=false). "
            "To enable: da.configure with allow_remote_put=true (or use local upload path).",
            category="POLICY_BLOCKED",
            feature="da.remote_put",
            allow_remote_put=False,
            effective_dir=store.root_dir,
            remediation=(
                "Set allow_remote_put=true via da.configure, "
                "or upload blobs locally via the node's file-system API."
            ),
        )


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
      ok, reason, enabled, writable, dir, effective_dir,
      max_bytes, used_bytes_da, used_bytes, free_bytes_fs,
      blob_count, last_error, last_error_code,
      peer_serving, allow_remote_get, allow_remote_put,
      policy_blocked_reason, eviction_policy, on_full,
      version
    }
    """
    try:
        requested_dir = None
        if isinstance(params, dict):
            requested_dir = params.get("dir")
        elif isinstance(params, (list, tuple)) and params:
            requested_dir = params[0]
        store = _get_store(requested_dir)
        cfg = store.config
        stats = store.stats()
        enabled = bool(cfg.enabled)
        # Check writeability
        writable = False
        if enabled:
            try:
                writable = os.access(store.root_dir, os.W_OK)
            except Exception:
                writable = False
        policy_blocked_reason = None
        if not enabled:
            policy_blocked_reason = "DA store is disabled"
        elif not cfg.allow_remote_put:
            policy_blocked_reason = "allow_remote_put=false: remote blob uploads are blocked by policy"
        ok = enabled and writable
        reason = None if ok else ("not_configured" if not enabled else "not_writable")
        return {
            "ok": ok,
            "reason": reason,
            "enabled": enabled,
            "writable": writable,
            "dir": store.root_dir,
            "effective_dir": store.root_dir,
            "max_bytes": cfg.max_bytes,
            "used_bytes_da": stats.get("used_bytes", 0),
            "used_bytes": stats.get("used_bytes", 0),  # backward compat alias
            "free_bytes_fs": stats.get("free_bytes_fs", 0),
            "blob_count": stats.get("blob_count", 0),
            "last_error": None,
            "last_error_code": None,
            "peer_serving": cfg.allow_remote_get,
            "allow_remote_get": cfg.allow_remote_get,
            "allow_remote_put": cfg.allow_remote_put,
            "policy_blocked_reason": policy_blocked_reason,
            "eviction_policy": cfg.eviction_policy,
            "on_full": cfg.on_full,
            "version": _DA_VERSION,
        }
    except rpc_errors.RpcError:
        raise
    except Exception as exc:
        _log.warning("da.status failed: %s", exc)
        return {
            "ok": False,
            "reason": "not_supported",
            "enabled": False,
            "writable": False,
            "dir": _default_da_dir(),
            "effective_dir": _default_da_dir(),
            "max_bytes": 0,
            "used_bytes_da": 0,
            "used_bytes": 0,
            "free_bytes_fs": 0,
            "blob_count": 0,
            "last_error": str(exc),
            "last_error_code": type(exc).__name__,
            "peer_serving": False,
            "allow_remote_get": False,
            "allow_remote_put": False,
            "policy_blocked_reason": None,
            "eviction_policy": "lru",
            "on_full": "evict",
            "version": _DA_VERSION,
        }



@method("da.getDefaultDir", aliases=("da_getDefaultDir",), desc="Get default node-side DA directory")
def da_get_default_dir(params=None, *_args, **_kwargs) -> dict:
    _ = params
    return {"dir": _default_da_dir()}


@method("da.getAllowedBaseDirs", aliases=("da_getAllowedBaseDirs",), desc="Get allowed base directories for DA store")
def da_get_allowed_base_dirs(params=None, *_args, **_kwargs) -> dict:
    _ = params
    return {"dirs": _allowed_base_dirs()}


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
    # Normalise params: accept object params, legacy [object], or legacy positional list.
    received_keys: list[str] = []
    if isinstance(params, dict):
        kwargs.update(params)
        received_keys = sorted(str(k) for k in params.keys())
    elif isinstance(params, (list, tuple)) and params:
        if isinstance(params[0], dict):
            kwargs.update(params[0])
            received_keys = sorted(str(k) for k in params[0].keys())
        else:
            ordered = ["enabled", "dir", "max_bytes", "on_full", "allow_remote_put"]
            for idx, value in enumerate(params):
                if idx < len(ordered):
                    kwargs.setdefault(ordered[idx], value)
            received_keys = [ordered[idx] for idx in range(min(len(params), len(ordered)))]
    else:
        received_keys = sorted(str(k) for k in kwargs.keys()) if kwargs else []

    _log.debug("da.configure parse params_type=%s received_keys=%s kwargs_keys=%s", type(params).__name__, received_keys, sorted(kwargs.keys()))

    # Validate
    enabled = kwargs.get("enabled")
    da_dir = kwargs.get("dir")
    max_bytes = kwargs.get("max_bytes", kwargs.get("limit_bytes"))
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

    if enabled is None:
        raise rpc_errors.InvalidParams(
            "Missing required parameter: enabled",
            data={
                "reason": "missing_enabled",
                "error_code": "DA_CONFIG_MISSING_REQUIRED",
                "received_keys": received_keys,
                "parsed_keys": sorted(str(k) for k in kwargs.keys()),
            },
        )
    if bool(enabled):
        if not isinstance(da_dir, str) or not da_dir.strip():
            raise rpc_errors.InvalidParams(
                "Missing required parameter: dir",
                data={"reason": "missing_dir", "error_code": "DA_CONFIG_MISSING_REQUIRED"},
            )
        if max_bytes is None:
            raise rpc_errors.InvalidParams(
                "Missing required parameter: max_bytes",
                data={"reason": "missing_max_bytes", "error_code": "DA_CONFIG_MISSING_REQUIRED"},
            )

    # Resolve directory
    try:
        root = os.path.abspath(da_dir) if da_dir else _default_da_dir()
        allowed_dirs = _allowed_base_dirs()
        if not _is_allowed_dir(root, allowed_dirs):
            raise rpc_errors.InvalidParams(
                f"DA directory must be under one of: {allowed_dirs}",
                data={"reason": "invalid_dir", "error_code": "DA_CONFIG_DIR_NOT_ALLOWED", "dir": root, "allowed_base_dirs": allowed_dirs},
            )
        Path(root).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        if isinstance(exc, rpc_errors.RpcError):
            raise
        msg = str(exc)
        if "Read-only file system" in msg or "Errno 30" in msg:
            raise rpc_errors.InvalidParams(
                f"Cannot create DA directory: {exc}. Node runs in a container; use a writable path under /data (for example /data/da).",
                data={"reason": "dir_create_failed", "error_code": "DA_CONFIG_DIR_CREATE_FAILED", "dir": root},
            )
        raise rpc_errors.InvalidParams(
            f"Cannot create DA directory: {exc}",
            data={"reason": "dir_create_failed", "error_code": "DA_CONFIG_DIR_CREATE_FAILED", "dir": root},
        )

    if not os.access(root, os.W_OK):
        raise rpc_errors.InvalidParams(
            f"DA directory is not writable: {root}",
            data={"reason": "not_writable", "error_code": "DA_CONFIG_DIR_NOT_WRITABLE", "dir": root},
        )

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

    try:
        store.update_config(**update_kwargs)
    except Exception as exc:
        raise rpc_errors.InternalError(
            f"Failed to persist DA config: {exc}",
            data={"reason": "persist_failed", "error_code": "DA_CONFIG_PERSIST_FAILED", "dir": root},
        )

    persisted_payload = {
        "enabled": bool(store.config.enabled),
        "dir": root,
        "max_bytes": int(store.config.max_bytes),
        "allow_remote_get": bool(store.config.allow_remote_get),
        "allow_remote_put": bool(store.config.allow_remote_put),
        "eviction_policy": str(store.config.eviction_policy),
        "on_full": str(store.config.on_full),
    }
    try:
        _persist_da_config(persisted_payload)
    except Exception as exc:
        raise rpc_errors.InternalError(
            f"Failed to persist DA runtime config: {exc}",
            data={"reason": "persist_runtime_failed", "error_code": "DA_CONFIG_PERSIST_FAILED", "dir": root},
        )

    # Get current status after update
    status = da_status({"dir": root})

    # Build requested vs effective comparison
    cfg_after = store.config
    requested: Dict[str, Any] = {}
    if enabled is not None:
        requested["enabled"] = bool(enabled)
    if da_dir is not None:
        requested["dir"] = da_dir
    if max_bytes is not None:
        requested["max_bytes"] = int(max_bytes)
    if eviction_policy:
        requested["eviction_policy"] = eviction_policy
    if on_full:
        requested["on_full"] = on_full
    if allow_remote_get is not None:
        requested["allow_remote_get"] = bool(allow_remote_get)
    if allow_remote_put is not None:
        requested["allow_remote_put"] = bool(allow_remote_put)

    effective: Dict[str, Any] = {
        "enabled": cfg_after.enabled,
        "dir": root,
        "max_bytes": cfg_after.max_bytes,
        "eviction_policy": cfg_after.eviction_policy,
        "on_full": cfg_after.on_full,
        "allow_remote_get": cfg_after.allow_remote_get,
        "allow_remote_put": cfg_after.allow_remote_put,
    }

    # Check for overrides/warnings
    warnings: List[str] = []
    overrides_applied: List[str] = []
    if enabled is True and not cfg_after.enabled:
        warnings.append(
            "requested enabled=true but effective enabled=false: "
            "store may require a valid dir and write access"
        )
    if allow_remote_put is True and not cfg_after.allow_remote_put:
        warnings.append(
            "requested allow_remote_put=true but policy disallows it; "
            "check node policy configuration"
        )

    if bool(enabled) and (not status.get("enabled") or not status.get("writable")):
        raise rpc_errors.InternalError(
            "Failed to enable DA after configuration",
            data={
                "reason": str(status.get("reason") or status.get("policy_blocked_reason") or "enable_failed"),
                "error_code": "DA_ENABLE_FAILED",
                "status": status,
            },
        )

    status["requested"] = requested
    status["effective"] = effective
    status["policy"] = {
        "allow_remote_put": cfg_after.allow_remote_put,
        "allow_remote_get": cfg_after.allow_remote_get,
        "on_full": cfg_after.on_full,
        "eviction_policy": cfg_after.eviction_policy,
    }
    status["overrides_applied"] = overrides_applied
    status["warnings"] = warnings
    return status


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
        _require_remote_put_allowed(store)
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