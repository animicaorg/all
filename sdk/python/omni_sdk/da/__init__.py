"""
omni_sdk.da
===========

High-level Data Availability (DA) client utilities for posting blobs,
retrieving them by commitment, and verifying availability proofs against
the DA service mounted on the node RPC.

Typical usage
-------------
    from omni_sdk.rpc.http import HttpClient
    from omni_sdk.da import DAClient

    rpc = HttpClient("http://127.0.0.1:8545")
    da = DAClient(rpc)

    # Post a blob under a namespace; receive a commitment and receipt
    commit, receipt = da.post_blob(namespace=24, data=b"...payload...")

    # Fetch by commitment
    data = da.get_blob(commit)

    # Ask the DA service for a sampling/availability proof
    ok = da.verify_availability(commit)

This package re-exports :class:`DAClient` from :mod:`omni_sdk.da.client`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export package version if available
try:  # pragma: no cover
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["DAClient", "__version__"]

# Lazy re-export to avoid import-time errors before client.py exists.
if TYPE_CHECKING:
    from .client import DAClient  # type: ignore
else:
    def __getattr__(name: str):
        if name == "DAClient":
            from .client import DAClient  # type: ignore
            return DAClient
        raise AttributeError(name)
