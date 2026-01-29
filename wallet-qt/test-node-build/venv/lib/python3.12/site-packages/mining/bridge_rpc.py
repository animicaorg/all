from __future__ import annotations

"""
Adapter to mount the miner getwork/submit WebSocket into the main FastAPI RPC app.

Usage (inside your RPC server factory):

    from fastapi import FastAPI
    from mining.bridge_rpc import mount_getwork, wire_submitter, wire_template_provider

    app = FastAPI()
    mount_getwork(app, path="/ws/getwork")  # mounts the WS endpoint

    # Option A: wire to your real submitter/template provider
    wire_submitter(lambda params: submitter.submit(params))          # async or sync callable OK
    wire_template_provider(async_get_latest_template, interval_sec=1)

    # Option B: convenience: wire to mining.share_submitter / mining.templates if available
    from mining.bridge_rpc import wire_from_components
    wire_from_components(
        submitter=share_submitter_instance,              # must expose async def submit(params)->dict
        template_provider=template_builder_instance      # must expose async def current_template()->Optional[dict]
    )

This keeps mining/ws_getwork.py decoupled and lets RPC choose how to feed work and accept shares.
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional

try:
    from fastapi import FastAPI  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("bridge_rpc requires FastAPI to be installed") from e

from .ws_getwork import JSON, WSGetWorkHub, get_hub, install_template_provider
from .ws_getwork import router as getwork_router
from .ws_getwork import start_background_broadcaster, wire_submit_callback

# Public type aliases
SubmitCallback = Callable[[JSON], Awaitable[JSON]]  # params → JSON result
TemplateProvider = Callable[
    [], Awaitable[Optional[JSON]]
]  # returns latest template or None


def mount_getwork(app: FastAPI, *, path: str = "/ws/getwork") -> WSGetWorkHub:
    """
    Mount the WebSocket endpoint that serves miner.getWork and miner.submitShare.

    - Registers the /ws/getwork route (or custom path via `path`).
    - Starts the broadcaster task on app startup.

    Returns the WS hub for further configuration if desired.
    """
    hub = get_hub()

    # Re-mount the router under custom path if requested
    # The router is defined with "/ws/getwork" by default; if the user wants a different path,
    # mount an additional route that delegates to the same hub.
    if path != "/ws/getwork":
        from fastapi import APIRouter, WebSocket  # type: ignore

        alt = APIRouter()

        @alt.websocket(path)
        async def _alt(ws: WebSocket) -> None:  # type: ignore
            await hub.serve_ws(ws)

        app.include_router(alt)
    else:
        app.include_router(getwork_router)  # default route

    @app.on_event("startup")
    async def _start_broadcaster() -> None:
        asyncio.create_task(hub.run_broadcaster())

    return hub


def wire_submitter(cb: SubmitCallback) -> None:
    """
    Install the callback that handles miner.submitShare(params) → result JSON.

    The callback may be:
      - async def(params) -> dict
      - sync def(params) -> dict (will be awaited transparently)

    Expected result shape (see mining.ws_getwork.SubmitResult.to_json for details):
      {"accepted": bool, "isBlock": bool, "reason"?: str, "newHead"?: {...}}
    """

    async def _maybe_await(params: JSON) -> JSON:
        res = cb(params)
        if asyncio.iscoroutine(res):  # type: ignore
            return await res  # type: ignore
        return res  # type: ignore

    wire_submit_callback(_maybe_await)


def wire_template_provider(
    provider: TemplateProvider, *, interval_sec: float = 1.0
) -> asyncio.Task:
    """
    Periodically poll the given coroutine to obtain the latest work template
    and broadcast miner.newWork when it changes.

    The provider must be:
      async def provider() -> Optional[dict]
    """
    return install_template_provider(provider, interval_sec=interval_sec)


# -------------------- Convenience wiring for common components --------------------


def wire_from_components(
    *,
    submitter: Any,
    template_provider: Any,
    interval_sec: float = 1.0,
) -> None:
    """
    Convenience for common mining components without coupling to concrete classes.

    Requirements:
      - `submitter` exposes an async method `submit(params: dict) -> dict`
        (or sync; will be awaited if needed).
      - `template_provider` exposes an async method `current_template() -> Optional[dict]`
        (or sync), returning a JSON-serializable work template.

    The template's `jobId` field is used to detect changes; when it changes,
    a miner.newWork notification is broadcast automatically.
    """

    async def _submit_cb(params: JSON) -> JSON:
        fn = getattr(submitter, "submit", None)
        if fn is None:
            raise RuntimeError("submitter has no 'submit(params)' method")
        res = fn(params)
        if asyncio.iscoroutine(res):
            return await res
        return res

    async def _provider_cb() -> Optional[JSON]:
        fn = getattr(template_provider, "current_template", None)
        if fn is None:
            # Try a more generic 'get' or 'build' method name
            fn = getattr(template_provider, "get", None) or getattr(
                template_provider, "build", None
            )
        if fn is None:
            raise RuntimeError(
                "template_provider has no 'current_template()' (or get/build) method"
            )
        res = fn()
        if asyncio.iscoroutine(res):
            return await res
        return res  # type: ignore

    wire_submitter(_submit_cb)
    wire_template_provider(_provider_cb, interval_sec=interval_sec)


# -------------------- One-call integration helper --------------------


def integrate_getwork(
    app: FastAPI,
    *,
    path: str = "/ws/getwork",
    submit_cb: Optional[SubmitCallback] = None,
    template_cb: Optional[TemplateProvider] = None,
    interval_sec: float = 1.0,
) -> WSGetWorkHub:
    """
    High-level helper:
      - Mount the WS endpoint at `path`
      - Optionally wire submit and template callbacks
      - Start the broadcaster on app startup

    Example:
        hub = integrate_getwork(app,
                                submit_cb=my_submit,
                                template_cb=my_template_provider,
                                interval_sec=0.5)
    """
    hub = mount_getwork(app, path=path)
    if submit_cb is not None:
        wire_submitter(submit_cb)
    if template_cb is not None:
        # we schedule provider after app startup to ensure event loop exists
        @app.on_event("startup")
        async def _wire_tpl() -> None:
            wire_template_provider(template_cb, interval_sec=interval_sec)

    return hub
