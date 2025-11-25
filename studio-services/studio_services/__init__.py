"""
Animica Studio Services
=======================

Thin FastAPI-based proxy for deploy / verify / faucet / artifacts.

This package exposes:

- ``__version__``: semantic version string
- ``build_app()``: convenience creator for a configured FastAPI app

Prefer importing submodules directly for specific concerns:
``studio_services.config``, ``studio_services.logging``,
``studio_services.routers.*``, etc.
"""

from __future__ import annotations

from .version import __version__

__all__ = ["__version__", "build_app"]


def build_app():
    """
    Create and return a fully configured FastAPI application.

    Notes
    -----
    This is a light wrapper around :func:`studio_services.app.create_app`.
    Importing lazily avoids importing FastAPI (and related deps) when
    consumers only need version metadata.
    """
    # Lazy import to keep package import side-effect free & fast.
    from .app import create_app

    return create_app()
