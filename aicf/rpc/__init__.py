from __future__ import annotations

"""
aicf.rpc
--------

Package marker and lightweight exports for the AICF (AI/Quantum Coordination
Framework) RPC surface.

This subpackage will host:
  • HTTP (FastAPI) route mounting for read-only endpoints (providers, jobs, payouts)
  • Optional WebSocket notifications (e.g., job assignments/completions, slashes)
"""

from typing import Dict, Final

# Base path under which AICF endpoints are mounted into the node's primary API.
RPC_PREFIX: Final[str] = "/aicf"

# Suggested OpenAPI tag used by route modules in this package.
AICF_OPENAPI_TAG: Final[Dict[str, str]] = {
    "name": "aicf",
    "description": "AI/Quantum provider registry, job queue, and settlements (read-only).",
}

__all__ = [
    "RPC_PREFIX",
    "AICF_OPENAPI_TAG",
]
