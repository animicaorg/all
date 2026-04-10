"""
capabilities.rpc.mount
======================

Helper to mount the Capabilities RPC (HTTP + WebSocket) into an existing
FastAPI application.

This is intentionally lightweight and import-safe (it only imports FastAPI
and the route factories when you call `mount`).

Example
-------
    from fastapi import FastAPI
    from capabilities.rpc.mount import mount

    app = FastAPI()
    mount(app, prefix="/cap")  # HTTP at /cap/* and WS at /cap/ws
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

__all__ = ["mount"]

log = logging.getLogger(__name__)


def _ensure_state_set(app: Any) -> set[str]:
    """
    Ensure we have a set on app.state to track mounted prefixes and return it.
    Works with bare FastAPI apps that may not have a 'state' attribute yet.
    """
    # FastAPI exposes `app.state` (a Starlette State). Fall back gracefully.
    state = getattr(app, "state", None)
    if state is None:
        # Create a simple shim if we're not on Starlette/FastAPI for any reason.
        class _Shim:
            pass

        state = _Shim()
        setattr(app, "state", state)

    key = "_capabilities_rpc_mounted"
    mounted: Optional[set[str]] = getattr(state, key, None)
    if mounted is None:
        mounted = set()
        setattr(state, key, mounted)
    return mounted


def mount(
    app: Any, prefix: str = "/cap", *, tags: Optional[Iterable[str]] = None
) -> None:
    """
    Mount Capabilities HTTP and WS routes into an existing FastAPI app.

    Parameters
    ----------
    app : fastapi.FastAPI
        The target application.
    prefix : str
        URL prefix for the HTTP routes (default: "/cap"). The WebSocket route
        is registered at f"{prefix}/ws".
    tags : Iterable[str] | None
        Optional tags applied to the HTTP router for OpenAPI grouping.

    Notes
    -----
    - Idempotent per (app, prefix): repeated calls with the same prefix
      are ignored.
    - This performs imports lazily to avoid pulling FastAPI/msgspec/etc.
      at package import time.
    """
    mounted = _ensure_state_set(app)
    if prefix in mounted:
        log.debug("capabilities.rpc already mounted at prefix %s; skipping", prefix)
        return

    try:
        # Import here to keep module import light.
        from .methods import router as http_router_factory  # type: ignore
        from .ws import router as ws_router_factory  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Failed to import capabilities RPC routers. "
            "Ensure capabilities.rpc.methods and capabilities.rpc.ws are available."
        ) from e

    # Build routers
    http_router = http_router_factory()
    ws_router = ws_router_factory()

    # Include into the app
    include_router = getattr(app, "include_router", None)
    if include_router is None:
        raise TypeError(
            "Provided 'app' does not look like a FastAPI app: missing include_router()."
        )

    include_kwargs = {}
    if tags is not None:
        include_kwargs["tags"] = list(tags)

    app.include_router(http_router, prefix=prefix, **include_kwargs)
    app.include_router(ws_router, prefix=prefix)

    mounted.add(prefix)
    log.info("Mounted capabilities RPC at prefix %s (HTTP + WS)", prefix)
