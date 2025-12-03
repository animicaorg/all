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


__all__ = ["da_put_blob", "da_get_blob", "da_get_proof"]
