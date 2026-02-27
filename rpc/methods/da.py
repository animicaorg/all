"""Data availability RPC surface — node-side implementation."""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rpc import errors as rpc_errors
from rpc import deps
from rpc.methods import method

_log = logging.getLogger("animica.rpc.da")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DA_VERSION = "1.0.0"
_MAX_PUT_BYTES = 32 * 1024 * 1024  # 32 MiB
_INGEST_RATE_LIMIT_SECONDS = 0.2
_last_ingest_at: dict[str, float] = {}
_DOCKER_BRIDGE_CIDR = "172.16.0.0/12"


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


def _default_ingest_dir(store_root: str) -> str:
    chain_root = os.path.dirname(os.path.abspath(store_root))
    return os.path.join(chain_root, "da_ingest")


def _resolve_ingest_dir(store) -> str:
    cfg_path = os.path.join(store.root_dir, "config.json")
    configured = None
    try:
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
                if isinstance(raw, dict):
                    configured = raw.get("ingest_dir")
    except Exception:
        configured = None

    ingest_dir = str(configured).strip() if isinstance(configured, str) else ""
    if not ingest_dir:
        ingest_dir = _default_ingest_dir(store.root_dir)
    return os.path.abspath(ingest_dir)


def _resolve_data_root(store) -> str:
    ingest_dir = _resolve_ingest_dir(store)
    chain_root = os.path.dirname(os.path.abspath(store.root_dir))
    if chain_root.startswith("/data/") or chain_root == "/data":
        return "/data"
    return os.path.dirname(chain_root)


def _assert_safe_ingest_path(path: str, ingest_dir: str) -> str:
    candidate = os.path.abspath(path)
    if not (candidate == ingest_dir or candidate.startswith(f"{ingest_dir}{os.sep}")):
        raise rpc_errors.RpcError(
            -32005,
            "ingest path must be under configured ingest directory",
            {
                "category": "INVALID_PATH",
                "feature": "da.local_ingest",
                "ingest_dir": ingest_dir,
                "path": candidate,
            },
        )
    if os.path.islink(candidate):
        raise rpc_errors.RpcError(
            -32005,
            "symlink paths are not allowed for local ingest",
            {
                "category": "INVALID_PATH",
                "feature": "da.local_ingest",
                "path": candidate,
            },
        )
    return candidate


def _is_local_rpc_request(rpc_ctx: Any = None) -> bool:
    return _authorize_local_ingest_request(rpc_ctx)["allowed"]


def _remote_rpc_ip(rpc_ctx: Any = None) -> str | None:
    """Extract the remote peer IP from the per-request RPC context.

    Accepts an explicit ``rpc_ctx`` (a ``rpc.jsonrpc.Context`` with a
    ``client`` tuple) so that method handlers can forward the actual HTTP
    client info.  Falls back to ``deps.get_ctx()`` for backwards-compat
    with code that does not forward the context (older call sites / tests).
    """
    # Prefer the explicitly-provided per-request context (jsonrpc.Context).
    contexts_to_try = []
    if rpc_ctx is not None:
        contexts_to_try.append(rpc_ctx)
    try:
        contexts_to_try.append(deps.get_ctx())
    except Exception:
        pass

    for ctx in contexts_to_try:
        try:
            client = getattr(ctx, "client", None)
            host = None
            if isinstance(client, tuple) and client:
                host = client[0]
            elif client is not None:
                host = getattr(client, "host", None)
            if not host:
                continue
            return str(ipaddress.ip_address(str(host)))
        except Exception:
            continue

    # Direct in-process method calls (tests/internal) have no request context.
    # Return None so call sites can distinguish unresolved transport peers.
    return None


def _rpc_bound_localhost_only() -> bool:
    host = (os.getenv("ANIMICA_RPC_HOST", "127.0.0.1") or "").strip().lower()
    return host in {"", "127.0.0.1", "localhost", "::1"}


def _allowed_local_rpc_nets() -> list[str]:
    raw = (os.getenv("ANIMICA_DA_INGEST_ALLOWED_NETS") or os.getenv("ANIMICA_ALLOWED_LOCAL_RPC_NETS") or "").strip()
    if raw:
        return [entry.strip() for entry in raw.split(",") if entry.strip()]
    allowed = ["127.0.0.1/32", "::1/128"]
    docker_explicit = _bool_env("ANIMICA_DA_ALLOW_DOCKER_BRIDGE", False)
    if _is_container_runtime() or docker_explicit:
        if _rpc_bound_localhost_only() or _bool_env("ANIMICA_DA_ALLOW_DOCKER_BRIDGE_UNSAFE", False):
            allowed.append(_DOCKER_BRIDGE_CIDR)
    return allowed


def _valid_ingest_token(rpc_ctx: Any = None) -> bool:
    token = (os.getenv("ANIMICA_DA_INGEST_TOKEN") or "").strip()
    if not token:
        return False
    contexts_to_try = []
    if rpc_ctx is not None:
        contexts_to_try.append(rpc_ctx)
    try:
        contexts_to_try.append(deps.get_ctx())
    except Exception:
        pass
    for ctx in contexts_to_try:
        try:
            headers = getattr(ctx, "headers", {}) or {}
            provided = str(headers.get("x-animica-ingest-token") or "").strip()
            if provided and provided == token:
                return True
        except Exception:
            continue
    return False


def _authorize_local_ingest_request(rpc_ctx: Any = None) -> dict[str, Any]:
    remote_ip = _remote_rpc_ip(rpc_ctx)
    allowed_cidrs = _allowed_local_rpc_nets()
    ip_allowed = False
    if remote_ip:
        try:
            remote = ipaddress.ip_address(remote_ip)
            ip_allowed = any(remote in ipaddress.ip_network(cidr, strict=False) for cidr in allowed_cidrs)
        except Exception:
            ip_allowed = False
    token_allowed = _valid_ingest_token(rpc_ctx)
    return {
        "allowed": bool(ip_allowed or token_allowed),
        "remote_ip": remote_ip,
        "allowed_nets": allowed_cidrs,
        "token_configured": bool((os.getenv("ANIMICA_DA_INGEST_TOKEN") or "").strip()),
        "token_valid": token_allowed,
        "reason": "no_transport_peer" if not remote_ip else None,
    }


def _ingest_local_guard_enabled() -> bool:
    return _bool_env("ANIMICA_DA_INGEST_LOCAL_ONLY", True)


def _enforce_ingest_rate_limit(ingest_dir: str) -> None:
    now = time.monotonic()
    last = _last_ingest_at.get(ingest_dir, 0.0)
    if now - last < _INGEST_RATE_LIMIT_SECONDS:
        raise rpc_errors.TooManyRequests(
            "Local ingest rate limit exceeded",
            retry_after=max(_INGEST_RATE_LIMIT_SECONDS - (now - last), 0.0),
        )
    _last_ingest_at[ingest_dir] = now


def _pending_debug_files(ingest_dir: str, limit: int = 5) -> list[str]:
    pending_dir = os.path.join(ingest_dir, "pending")
    if not os.path.isdir(pending_dir):
        return []
    try:
        names = sorted(os.listdir(pending_dir))[:limit]
    except Exception:
        return []
    return [os.path.join(pending_dir, name) for name in names]


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
        # Reject the exact base dir root (e.g. /data) — require a subdirectory.
        # allowed_dirs entries are already normalized by _allowed_base_dirs(), so direct compare is safe.
        for base in allowed_dirs:
            if root == base:
                raise rpc_errors.InvalidParams(
                    f"Node DA dir cannot be the base directory root {root!r}; "
                    f"use a subdirectory such as {root}/chain-<id>/da",
                    data={"reason": "dir_must_be_subdir", "error_code": "DA_CONFIG_DIR_ROOT", "dir": root},
                )
        Path(root).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        if isinstance(exc, rpc_errors.RpcError):
            raise
        if isinstance(exc, PermissionError):
            raise rpc_errors.DaConfigPermDenied(
                path=root,
                error_code="DA_CONFIG_PERMISSION_DENIED",
            )
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
        raise rpc_errors.DaConfigPermDenied(
            path=root,
            error_code="DA_CONFIG_PERMISSION_DENIED",
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


@method("da.getIngestDir", aliases=("da_getIngestDir",), desc="Get node local ingest directory for DA blobs")
def da_get_ingest_dir(params=None, **kwargs) -> dict:
    _ = params, kwargs
    store = _require_store()
    ingest_dir = _resolve_ingest_dir(store)
    Path(ingest_dir).mkdir(parents=True, exist_ok=True)
    return {
        "dir": ingest_dir,
        "pending_dir": os.path.join(ingest_dir, "pending"),
        "ingested_dir": os.path.join(ingest_dir, "ingested"),
    }


@method("da.getDataRoot", aliases=("da_getDataRoot",), desc="Get node filesystem data root used for DA path mapping")
def da_get_data_root(params=None, **kwargs) -> dict:
    _ = params, kwargs
    store = _require_store()
    return {
        "data_root": _resolve_data_root(store),
        "node_chain_dir": os.path.abspath(store.root_dir),
    }


@method("da.statPath", aliases=("da_statPath",), desc="Check if a node-local path exists")
def da_stat_path(params=None, **kwargs) -> dict:
    if isinstance(params, dict):
        kwargs.update(params)
    elif isinstance(params, (list, tuple)) and params:
        kwargs["path"] = params[0]

    path = kwargs.get("path")
    if not isinstance(path, str) or not path.strip():
        raise rpc_errors.InvalidParams("Missing required parameter: path")

    candidate = os.path.abspath(path.strip())
    return {
        "path": candidate,
        "exists": os.path.exists(candidate),
        "is_file": os.path.isfile(candidate),
        "is_dir": os.path.isdir(candidate),
    }


@method("da.getHostMountHints", aliases=("da_getHostMountHints",), desc="Best-effort mount hints for containerized DA deployments")
def da_get_host_mount_hints(params=None, **kwargs) -> dict:
    _ = params, kwargs
    store = _require_store()
    chain_root = os.path.dirname(os.path.abspath(store.root_dir))
    chain_subdir = os.path.basename(chain_root)
    return {
        "node_data_root": _resolve_data_root(store),
        "chain_subdir": chain_subdir,
        "expected_host_chain_dir": "<unknown>",
        "notes": "If running docker, mount host ~/.animica to /data",
    }


@method("da.ingestLocal", aliases=("da_ingestLocal",), desc="Ingest a local blob file from the node ingest directory")
def da_ingest_local(params=None, ctx=None, **kwargs) -> dict:
    if isinstance(params, dict):
        kwargs.update(params)
    elif isinstance(params, (list, tuple)) and params:
        if isinstance(params[0], dict):
            kwargs.update(params[0])
        else:
            kwargs["path"] = params[0]
            if len(params) > 1:
                kwargs["namespace"] = params[1]

    path = kwargs.get("path")
    if not isinstance(path, str) or not path.strip():
        raise rpc_errors.InvalidParams("Missing required parameter: path")

    auth = _authorize_local_ingest_request(ctx)
    if _ingest_local_guard_enabled() and not auth["allowed"]:
        raise rpc_errors.RpcError(
            -32006,
            "permission denied: da.ingestLocal is restricted to local RPC callers",
            {
                "feature": "da.local_ingest",
                "local_only": True,
                "remote_ip": auth["remote_ip"],
                "allowed": auth["allowed_nets"],
                "token_configured": auth["token_configured"],
                "token_matched": auth["token_valid"],
                "reason": auth.get("reason"),
            },
        )

    store = _require_store()
    ingest_dir = _resolve_ingest_dir(store)
    _enforce_ingest_rate_limit(ingest_dir)
    safe_path = _assert_safe_ingest_path(path.strip(), ingest_dir)
    if not os.path.isfile(safe_path):
        raise rpc_errors.NotFound(
            f"ingest file not found: {safe_path}",
            ingest_dir=ingest_dir,
            cwd=os.getcwd(),
            ingest_dir_exists=os.path.isdir(ingest_dir),
            pending_examples=_pending_debug_files(ingest_dir),
        )

    try:
        with open(safe_path, "rb") as fh:
            blob_bytes = fh.read()
    except PermissionError as exc:
        raise rpc_errors.RpcError(-32006, f"permission denied reading ingest file: {exc}") from exc
    except Exception as exc:
        raise rpc_errors.InternalError(f"Unable to read ingest file: {exc}")

    if len(blob_bytes) > _MAX_PUT_BYTES:
        raise rpc_errors.InvalidParams(
            f"Blob too large: {len(blob_bytes)} > {_MAX_PUT_BYTES}"
        )

    metadata: dict[str, Any] = {
        "ingested_from": safe_path,
        "namespace": int(kwargs.get("namespace", 0) or 0),
        "ingested_via": "da.ingestLocal",
    }
    blob_id, size_bytes = store.put(blob_bytes, metadata=metadata)

    ingested_dir = os.path.join(ingest_dir, "ingested")
    Path(ingested_dir).mkdir(parents=True, exist_ok=True)
    moved_to = ""
    try:
        moved_to = os.path.join(ingested_dir, os.path.basename(safe_path))
        os.replace(safe_path, moved_to)
    except Exception:
        moved_to = ""

    return {
        "blob_id": blob_id,
        "size_bytes": size_bytes,
        "ingested": True,
        "source_path": safe_path,
        "moved_to": moved_to,
    }




@method("da.getCallerInfo", aliases=("da_getCallerInfo", "node.whoAmI", "node_whoAmI"), desc="Return caller network identity as seen by node")
def da_get_caller_info(params=None, ctx=None, **kwargs) -> dict:
    _ = params, kwargs
    auth = _authorize_local_ingest_request(ctx)
    return {
        "remote_ip": auth["remote_ip"],
        "is_loopback": bool(auth["remote_ip"]) and _is_local_rpc_request(ctx),
        "transport": "http",
        "rpc_bind": os.getenv("ANIMICA_RPC_HOST", "127.0.0.1"),
        "allowed_local_rpc_nets": auth["allowed_nets"],
        "rpc_bound_localhost_only": _rpc_bound_localhost_only(),
        "token_configured": auth["token_configured"],
        "token_matched": auth["token_valid"],
        "reason": auth.get("reason"),
    }

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
    "da_get_ingest_dir",
    "da_ingest_local",
    "da_get_caller_info",
    "da_get",
    "da_has",
    "da_list",
    "da_delete",
    "da_gc",
    "da_get_proof",
]
