"""
capabilities.rpc
================

Read-only RPC surface for capabilities:
- HTTP endpoints to inspect jobs/results
- WebSocket stream for job lifecycle events

This package intentionally keeps imports **lazy** so importing
`capabilities.rpc` has no side effects and does not pull FastAPI unless
you call the helpers below.

Typical usage
-------------
    from fastapi import FastAPI
    from capabilities.rpc import mount_into

    app = FastAPI()
    mount_into(app, prefix="/cap")  # mounts HTTP + WS endpoints

Exports
-------
- __version__: re-exported module version from `capabilities.version`
- get_http_router(): build and return the HTTP APIRouter
- get_ws_router(): build and return the WS APIRouter
- mount_into(app, prefix="/cap"): convenience to mount both
"""

from __future__ import annotations

from typing import Any

try:  # Re-export the package version for convenience
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    "__version__",
    "get_http_router",
    "get_ws_router",
    "mount_into",
]


def get_http_router() -> Any:
    """
    Return the FastAPI APIRouter exposing read-only capability endpoints.

    Imported lazily to avoid importing FastAPI at module import time.
    """
    # Local import to keep this package import-light
    from .methods import router as _router  # type: ignore

    return _router()


def get_ws_router() -> Any:
    """
    Return the FastAPI APIRouter exposing the WebSocket endpoint(s)
    for capability job events.
    """
    from .ws import router as _ws_router  # type: ignore

    return _ws_router()


def mount_into(app: Any, prefix: str = "/cap") -> None:
    """
    Mount both HTTP and WS routes into an existing FastAPI app.

    Parameters
    ----------
    app : fastapi.FastAPI
        The application instance to mount into.
    prefix : str
        URL prefix for the HTTP routes (default: "/cap"). The WS route
        will be mounted at f"{prefix}/ws".
    """
    from .mount import mount as _mount  # type: ignore

    _mount(app, prefix=prefix)
