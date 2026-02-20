"""Data availability RPC surface."""

from __future__ import annotations

import os
from pathlib import Path

from rpc import errors as rpc_errors
from rpc.methods import method


@method("da.putBlob", aliases=("da_putBlob",))
def da_put_blob(*_args, **_kwargs):
    raise rpc_errors.TemporarilyUnavailable("Blob submission not available")


@method("da.getBlob", aliases=("da_getBlob",))
def da_get_blob(*_args, **_kwargs):
    raise rpc_errors.TemporarilyUnavailable("Blob retrieval not available")


@method("da.getProof", aliases=("da_getProof",))
def da_get_proof(*_args, **_kwargs):
    raise rpc_errors.TemporarilyUnavailable("Blob proof not available")


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@method("da.status", aliases=("da_status", "da.getStatus", "da_getStatus"), desc="Get DA layer status")
def da_status(params=None, *_args, **_kwargs) -> dict:
    """Returns DA status in stable schema: {enabled, ok, reason, message, details}."""
    if isinstance(params, dict) and "params" in params:
        params = params.get("params")

    da_supported = _bool_env("ANIMICA_DA_SUPPORTED", True)
    if not da_supported:
        return {
            "enabled": False,
            "ok": False,
            "reason": "not_supported",
            "message": "DA is not supported in this node build. Use a DA-enabled node image/profile.",
            "details": {"supported": False},
        }

    da_enabled = _bool_env("ANIMICA_DA_ENABLED", False)
    storage_dir = Path(os.getenv("ANIMICA_DA_STORAGE_DIR", "./data/da")).expanduser().resolve()

    if not da_enabled:
        return {
            "enabled": False,
            "ok": False,
            "reason": "not_configured",
            "message": "DA is disabled/not configured. Set ANIMICA_DA_ENABLED=1 and configure ANIMICA_DA_STORAGE_DIR.",
            "details": {"storage_path": str(storage_dir)},
        }

    if not storage_dir.exists():
        return {
            "enabled": False,
            "ok": False,
            "reason": "not_configured",
            "message": f"DA storage directory does not exist: {storage_dir}. Create it and mount it read-write.",
            "details": {"storage_path": str(storage_dir)},
        }

    if not os.access(storage_dir, os.W_OK):
        return {
            "enabled": True,
            "ok": False,
            "reason": "read_only",
            "message": f"DA storage path is read-only: {storage_dir}. Remount as read-write.",
            "details": {"storage_path": str(storage_dir)},
        }

    backend_available = _bool_env("ANIMICA_DA_BACKEND_AVAILABLE", True)
    if not backend_available:
        return {
            "enabled": True,
            "ok": False,
            "reason": "service_unavailable",
            "message": "DA backend is enabled but currently unavailable. Verify DA service wiring and connectivity.",
            "details": {"storage_path": str(storage_dir)},
        }

    return {
        "enabled": True,
        "ok": True,
        "reason": None,
        "message": None,
        "details": {"storage_path": str(storage_dir)},
    }


__all__ = ["da_put_blob", "da_get_blob", "da_get_proof", "da_status"]
