"""
aicf_provider
=============

Tiny, well-typed package boundary for an Animica AI Compute Fund (AICF) provider.

This package is the *library half* of the provider template. The FastAPI app
(usually in ``app/main.py``) can import helpers from here without dragging in
framework dependencies at import time, which keeps CLIs and small tools snappy.

What lives here (recommended layout)
------------------------------------
- Version & package metadata helpers (no heavy imports).
- Lightweight logging utilities (no global side effects).
- Narrow, framework-agnostic datatypes used across the app (ProviderId,
  capabilities flags, result envelopes).
- Minimal constants for environment variable keys so they don’t drift.

Endpoints your app typically exposes (for reference)
----------------------------------------------------
- ``GET /healthz``              — liveness/readiness probe
- ``GET /version``              — version & build metadata
- ``POST /v1/jobs/claim``       — claim/lease a job from the queue (optional)
- ``POST /v1/jobs/complete``    — submit completion (digest/receipt/proof refs)
- ``GET /metrics``              — Prometheus (if you enable it)

Environment knobs (convention)
------------------------------
These are *not* parsed here to avoid importing config frameworks eagerly,
but we centralize the keys so other modules can import and agree on names.

- ``AICF_PROVIDER_ID``          — stable identifier for this provider
- ``AICF_CAP_AI``               — "1" to advertise AI capability
- ``AICF_CAP_QUANTUM``          — "1" to advertise Quantum capability
- ``AICF_QUEUE_URL``            — queue/broker base URL (if used)
- ``RPC_URL``                   — Animica node RPC (for verify/settlement hooks)
- ``LOG_LEVEL``                 — info|debug|warning|error

Design goals
------------
- **No side effects on import**: Do not configure logging or parse env here.
- **Zero heavy deps**: Standard library only in this module.
- **Small surface**: Keep exports explicit via ``__all__``.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as _pkg_version
import logging
from typing import Final, Mapping, Optional, TypedDict


# --------------------------------------------------------------------------------------
# Version helpers
# --------------------------------------------------------------------------------------

def _detect_version(dist_name: str = "aicf_provider") -> str:
    """
    Return the installed package version if available; otherwise a sane default.

    This works both when running from an installed wheel and straight from source
    (where importlib.metadata may not find a distribution).
    """
    try:
        return _pkg_version(dist_name)
    except PackageNotFoundError:
        # Keep this synced with pyproject.toml when you cut releases
        return "0.1.0"


__version__: Final[str] = _detect_version()
__description__: Final[str] = "Animica AICF Provider — FastAPI template core utilities"


def get_version() -> str:
    """Public accessor for the provider version (stable API for tests/health endpoints)."""
    return __version__


# --------------------------------------------------------------------------------------
# Lightweight types shared across app modules (no FastAPI/pydantic imports here)
# --------------------------------------------------------------------------------------

ProviderId = str


class CapabilitySet(TypedDict, total=False):
    """Feature flags advertised by the provider."""
    ai: bool
    quantum: bool


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    """
    Minimal identity & capability descriptor that endpoints can return
    (e.g., from /version or /identify).

    Avoids importing framework models; can be wrapped by response models upstream.
    """
    provider_id: ProviderId
    version: str
    capabilities: CapabilitySet


# --------------------------------------------------------------------------------------
# Logging utilities (no global configuration; just helpers)
# --------------------------------------------------------------------------------------

def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Return a logger with a NullHandler attached to avoid 'No handler found' warnings.
    Real configuration (formatters/levels/handlers) should be done by the app entrypoint.
    """
    logger = logging.getLogger(name if name else "aicf_provider")
    # Attach a NullHandler once; subsequent calls won't duplicate it
    if not any(isinstance(h, logging.NullHandler) for h in logger.handlers):
        logger.addHandler(logging.NullHandler())
    return logger


# --------------------------------------------------------------------------------------
# Environment key constants (shared names to reduce drift across modules)
# --------------------------------------------------------------------------------------

ENV: Final[Mapping[str, str]] = {
    "PROVIDER_ID": "AICF_PROVIDER_ID",
    "CAP_AI": "AICF_CAP_AI",
    "CAP_QUANTUM": "AICF_CAP_QUANTUM",
    "QUEUE_URL": "AICF_QUEUE_URL",
    "RPC_URL": "RPC_URL",
    "LOG_LEVEL": "LOG_LEVEL",
}


# --------------------------------------------------------------------------------------
# Friendly about() string for CLIs and /version endpoints
# --------------------------------------------------------------------------------------

def about() -> str:
    """
    Return a compact, single-line identity string suitable for logs or a /version endpoint.
    """
    return f"aicf_provider/{__version__} — {__description__}"


__all__ = [
    "__version__",
    "__description__",
    "get_version",
    "about",
    "get_logger",
    "ProviderId",
    "CapabilitySet",
    "ProviderInfo",
    "ENV",
]
