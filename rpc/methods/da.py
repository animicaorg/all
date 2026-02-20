"""
Data availability RPC surface.

These endpoints are placeholders to keep the JSON-RPC implementation aligned
with the OpenRPC spec. They currently return structured "temporarily
unavailable" errors until the DA service is wired up.
"""

from __future__ import annotations

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


@method("da.status", aliases=("da_status", "da.getStatus", "da_getStatus"), desc="Get DA layer status")
def da_status(*_args, **_kwargs) -> dict:
    """
    Returns the status of the DA (Data Availability) layer.

    Returns a stable schema: {enabled, ok, reason, message, details}.
    """
    return {
        "enabled": False,
        "ok": False,
        "reason": "unavailable",
        "message": "DA layer is not yet available on this node",
        "details": {},
    }


__all__ = ["da_put_blob", "da_get_blob", "da_get_proof", "da_status"]
