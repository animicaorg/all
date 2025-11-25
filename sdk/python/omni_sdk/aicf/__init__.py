"""
omni_sdk.aicf
=============

High-level client access to the AI Compute Fund (AICF) endpoints:
enqueue AI/Quantum jobs, inspect their status, and fetch results.

Typical usage
-------------
    from omni_sdk.rpc.http import HttpClient
    from omni_sdk.aicf import AICFClient

    rpc = HttpClient("http://127.0.0.1:8545")
    aicf = AICFClient(rpc)

    # Enqueue a tiny AI job
    job_id = aicf.enqueue_ai(model="tiny", prompt=b"hello world", fee=1234)

    # Read back result (raises if not yet available unless wait/poll is used)
    result = aicf.get_result(job_id)

This package re-exports :class:`AICFClient` from :mod:`omni_sdk.aicf.client`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export package version if present
try:  # pragma: no cover
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["AICFClient", "__version__"]

# Lazy re-export to avoid import-time side effects until needed.
if TYPE_CHECKING:
    from .client import AICFClient  # type: ignore
else:
    def __getattr__(name: str):
        if name == "AICFClient":
            from .client import AICFClient  # type: ignore
            return AICFClient
        raise AttributeError(name)
