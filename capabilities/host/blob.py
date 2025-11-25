"""
capabilities.host.blob
======================

Provider for blob-related host syscalls exposed to contracts.

Implements:
- blob_pin(ns, data) → returns a minimal receipt containing the DA commitment
  (NMT root), namespace, and size. Enforces size and namespace bounds.

This module prefers to delegate to `capabilities.adapters.da.pin_blob` if that
bridge is available (so the blob is actually persisted in the DA store).
If the adapter is unavailable, we compute the commitment locally as a best-effort
fallback to keep devnets/tests unblocked.

Return shape (dict):
{
    "namespace": int,
    "size": int,
    "commitment": bytes,     # NMT root (or dev-hash in fallback)
    "provider": str,         # "adapter.da" or "local.fallback"
}
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import logging

from .provider import SyscallContext, ProviderRegistry, get_registry, BLOB_PIN
from ..errors import CapError
from da.errors import NamespaceRangeError  # type: ignore

log = logging.getLogger("capabilities.host.blob")

# ----------------------------
# Limits & helpers (soft import)
# ----------------------------

# Max blob size (bytes). Prefer da.constants if present; fall back to 2 MiB.
try:
    from da.constants import MAX_BLOB_BYTES as _MAX_BLOB_BYTES  # type: ignore
except Exception:  # pragma: no cover
    _MAX_BLOB_BYTES = 2 * 1024 * 1024

# Namespace bounds: prefer da.nmt.namespace helpers; fall back to simple checks.
try:
    from da.nmt.namespace import validate_namespace_id as _validate_ns  # type: ignore
except Exception:  # pragma: no cover
    def _validate_ns(ns: int) -> None:
        if not isinstance(ns, int):
            raise NamespaceRangeError("namespace must be an int")
        if ns < 0 or ns > 0xFFFFFFFF:
            raise NamespaceRangeError("namespace out of range [0, 2^32-1]")


# Commitment calculator:
# First choice: real DA commitment (NMT) from da.blob.commitment.commit(data, namespace)
# Fallback: domain-separated SHA3-256(ns||len||data) — NOT CANONICAL, dev-only.
_COMMIT_MODE: Optional[str] = None
try:
    from da.blob.commitment import commit as _da_commit  # type: ignore
    _COMMIT_MODE = "da.commitment"
except Exception:  # pragma: no cover
    _da_commit = None
    _COMMIT_MODE = "fallback.sha3_256"
    import hashlib, struct

    def _fallback_commit(data: bytes, namespace: int) -> bytes:
        h = hashlib.sha3_256()
        h.update(b"animica:da:blob:commitment:fallback:v1")
        h.update(struct.pack(">I", namespace & 0xFFFFFFFF))
        h.update(struct.pack(">Q", len(data)))
        h.update(data)
        return h.digest()


# Optional DA adapter bridge
try:
    from ..adapters import da as _da_adapter  # type: ignore
    _HAS_ADAPTER = True
except Exception:  # pragma: no cover
    _da_adapter = None
    _HAS_ADAPTER = False


# ----------------------------
# Provider implementation
# ----------------------------

def _blob_pin(ctx: SyscallContext, *, namespace: int, data: bytes) -> Dict[str, Any]:
    """
    Validate and pin a blob. If the DA adapter is available, delegate to it so the blob
    is persisted and indexed. Otherwise, compute a commitment locally and return it.

    Raises:
        NamespaceRangeError, CapError (size/arguments), or adapter-specific errors.
    """
    # Basic type/limit checks
    if not isinstance(data, (bytes, bytearray)):
        raise CapError("blob_pin: data must be bytes")
    _validate_ns(namespace)

    size = len(data)
    if size <= 0:
        raise CapError("blob_pin: empty data not allowed")
    if size > int(_MAX_BLOB_BYTES):
        raise CapError(f"blob_pin: blob size {size} exceeds MAX_BLOB_BYTES={_MAX_BLOB_BYTES}")

    # Prefer the adapter (persists blob & returns canonical commitment)
    if _HAS_ADAPTER and hasattr(_da_adapter, "pin_blob"):
        log.debug("blob_pin: delegating to capabilities.adapters.da.pin_blob",
                  extra={"ns": namespace, "size": size, "height": ctx.height})
        result = _da_adapter.pin_blob(ctx, namespace=namespace, data=bytes(data))  # type: ignore[attr-defined]
        # Expect at least commitment (bytes), namespace (int), size (int)
        commit = result.get("commitment")
        if not isinstance(commit, (bytes, bytearray)):
            raise CapError("DA adapter returned invalid commitment type")
        return {
            "namespace": int(result.get("namespace", namespace)),
            "size": int(result.get("size", size)),
            "commitment": bytes(commit),
            "provider": "adapter.da",
        }

    # Local fallback: compute commitment only (no persistence).
    if _da_commit is not None:
        commit_bytes, committed_size, committed_ns = _da_commit(bytes(data), namespace)  # type: ignore[misc]
        return {
            "namespace": int(committed_ns),
            "size": int(committed_size),
            "commitment": bytes(commit_bytes),
            "provider": "local.da.commitment",
        }
    else:  # pragma: no cover - dev only
        commit_bytes = _fallback_commit(bytes(data), namespace)
        log.warning("blob_pin: using NON-CANONICAL fallback commitment (dev-only)",
                    extra={"ns": namespace, "size": size, "mode": _COMMIT_MODE})
        return {
            "namespace": namespace,
            "size": size,
            "commitment": commit_bytes,
            "provider": "local.fallback",
        }


# Mark as deterministic (hint checked by registry)
_blob_pin._deterministic = True  # type: ignore[attr-defined]


def register(registry: ProviderRegistry) -> None:
    """Register handlers into the provided registry."""
    registry.register(BLOB_PIN, _blob_pin)


# Auto-register on import (safe even if called multiple times)
try:  # pragma: no cover - trivial
    register(get_registry())
except Exception as _e:  # pragma: no cover
    # In unit tests, registry may not be initialized yet; that's fine.
    log.debug("blob provider auto-register skipped", extra={"reason": repr(_e)})


__all__ = ["register"]
