from __future__ import annotations

"""
aicf.rpc.mount
--------------

Helpers to mount the AICF RPC surface into an existing FastAPI app and/or to
register the JSON-RPC methods with your dispatcher.

Typical usage (REST):
    from fastapi import FastAPI
    from aicf.rpc.mount import mount_aicf
    from aicf.rpc.methods import ServiceBundle
    app = FastAPI()
    mount_aicf(app, service_bundle, prefix="/aicf")

Typical usage (JSON-RPC):
    from aicf.rpc.mount import register_jsonrpc
    register_jsonrpc(dispatcher, service_bundle)

Notes
-----
- This module is intentionally a light wrapper that wires the router produced
  by `aicf.rpc.methods.build_rest_router`.
- No hard dependency on a specific JSON-RPC framework: we just expect a
  dispatcher with a `.add(name, callable)` or `.register(name, callable)` API.
"""

from typing import Any, Protocol

from .methods import ServiceBundle, build_rest_router, make_methods


class _JsonRpcDispatcherLike(Protocol):
    """Minimal protocol to support common JSON-RPC dispatchers."""
    def add(self, method: str, func: Any) -> None: ...
    def register(self, method: str, func: Any) -> None: ...


def mount_aicf(app: Any, service: ServiceBundle, *, prefix: str = "/aicf") -> None:
    """
    Mount the AICF REST endpoints under `prefix` on a FastAPI app.

    Parameters
    ----------
    app : fastapi.FastAPI
        Your FastAPI application instance.
    service : ServiceBundle
        Bundle implementing registry/queue/treasury views.
    prefix : str
        URL prefix for the mounted router (default: "/aicf").
    """
    try:
        # We don't import FastAPI types at module import time to avoid hard deps.
        router = build_rest_router(service)
        app.include_router(router, prefix=prefix, tags=["aicf"])
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("Failed to mount AICF REST router") from exc


def register_jsonrpc(dispatcher: _JsonRpcDispatcherLike, service: ServiceBundle) -> None:
    """
    Register JSON-RPC methods on a dispatcher.

    We try `.add(name, fn)` first (popular in some libs) and fall back to
    `.register(name, fn)`.

    Parameters
    ----------
    dispatcher : object
        An object with either `add(name, fn)` or `register(name, fn)`.
    service : ServiceBundle
        Bundle implementing registry/queue/treasury views.
    """
    methods = make_methods(service)
    for name, fn in methods.items():
        # Prefer `add`, fall back to `register`.
        try:
            dispatcher.add(name, fn)  # type: ignore[attr-defined]
        except AttributeError:
            dispatcher.register(name, fn)  # type: ignore[attr-defined]


__all__ = ["mount_aicf", "register_jsonrpc"]
