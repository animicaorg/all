"""
Lightweight wrapper exposing the DA retrieval FastAPI application under the
``animica.da`` namespace.

Tests and services within the Animica Python tree expect ``create_app`` (or a
module-level ``app``) to be importable from ``animica.da.api``. We delegate the
actual endpoint wiring to :mod:`da.retrieval.api` so there is a single
implementation surface.
"""

from __future__ import annotations

from da.retrieval.api import create_app as _create_app

# Re-export the factory for callers that want their own service wiring.
create_app = _create_app

# Convenience: build a default app eagerly so test helpers can import a
# module-level object without needing to call the factory explicitly.
try:
    app = create_app()
except Exception:  # pragma: no cover - keep import robust in thin envs
    app = None

__all__ = ["create_app", "app"]
