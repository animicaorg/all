"""
Animica • DA • Retrieval package

This subpackage exposes a small FastAPI application for the Data Availability
(DA) retrieval service along with a lightweight client.

Modules
-------
- api.py       : FastAPI app factory (mounts POST /da/blob, GET /da/blob/{commitment}, /da/proof)
- service.py   : orchestration layer (store/NMT/erasure/proofs)
- handlers.py  : request/response marshaling
- auth.py      : optional tokens
- rate_limit.py: token-bucket limiter
- cache.py     : in-process caches for hot paths
- client.py    : HTTP client used by SDK/tests and other services

Public exports
--------------
- create_app()        : build and return a FastAPI app
- RetrievalService    : service facade for DA post/get/proof
- DAClient            : simple HTTP client for DA endpoints
- __version__         : version string from da.version
"""

from __future__ import annotations

try:
    from da.version import __version__  # re-export
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

# Re-export primary entry points for convenience.
from .api import create_app
from .service import RetrievalService
from .client import DAClient

__all__ = [
    "__version__",
    "create_app",
    "RetrievalService",
    "DAClient",
]
