from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import typing as t

from fastapi import (APIRouter, FastAPI, HTTPException, Request, Response,
                     WebSocket, WebSocketDisconnect)
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.cors import CORSMiddleware

# Local modules
from rpc import config as rpc_config
from rpc import deps
from rpc import errors as rpc_errors
from rpc import version as rpc_version

# Optional helpers (feature-detected)
_jsonrpc_mod = importlib.import_module("rpc.jsonrpc")
_ws_mod = importlib.import_module("rpc.ws")
_openrpc_mod = importlib.import_module("rpc.openrpc_mount")
_metrics_mod = importlib.import_module("rpc.metrics")

# -----------------------------------------------------------------------------
# Logger
# -----------------------------------------------------------------------------
log = logging.getLogger("animica.rpc.server")


# -----------------------------------------------------------------------------
# JSON-RPC integration (feature-detect common shapes)
# -----------------------------------------------------------------------------
# We support any of these from rpc/jsonrpc.py:
#   - jsonrpc_app: an ASGI app to mount directly
#   - get_app(): -> ASGI app
#   - dispatch(payload: dict|list) -> dict|list  (sync or async)
#   - handle(payload: dict|list) -> dict|list    (sync or async)
JSONRPC_ASGI_APP: t.Optional[t.Any] = None
JSONRPC_DISPATCH: t.Optional[t.Callable[..., t.Awaitable[t.Any] | t.Any]] = None

if hasattr(_jsonrpc_mod, "jsonrpc_app"):
    JSONRPC_ASGI_APP = getattr(_jsonrpc_mod, "jsonrpc_app")

elif hasattr(_jsonrpc_mod, "get_app"):
    try:
        JSONRPC_ASGI_APP = _jsonrpc_mod.get_app()  # type: ignore[assignment]
    except Exception:  # fallback to dispatch route
        JSONRPC_ASGI_APP = None

# Fallback callable dispatcher
for name in ("dispatch", "handle", "handle_request"):
    if hasattr(_jsonrpc_mod, name):
        JSONRPC_DISPATCH = getattr(_jsonrpc_mod, name)
        break


async def _call_dispatch(payload: t.Any, request: Request | None = None) -> t.Any:
    """Call the jsonrpc dispatcher which may be sync or async.

    The dispatcher in rpc.jsonrpc expects a Context object; when only a payload
    is provided we synthesize a minimal one so calls do not explode with a
    missing positional argument error (the root cause of the observed -32603
    Internal error when hitting /rpc).
    """
    if JSONRPC_DISPATCH is None:
        raise RuntimeError("JSON-RPC dispatcher not available")

    # Build a best-effort Context if the dispatcher signature requires it
    ctx = None
    try:
        from rpc.jsonrpc import Context, _default_ctx, _now_ms

        if request is None:
            ctx = Context(
                request=None, received_at_ms=_now_ms(), client=None, headers={}
            )
        else:
            client = request.client
            ctx = Context(
                request=request,
                received_at_ms=_now_ms(),
                client=(client.host, client.port) if client else None,  # type: ignore[arg-type]
                headers={k.lower(): v for k, v in request.headers.items()},
            )
    except Exception:
        ctx = None

    # Always provide some context to the dispatcher so integration points that
    # skip it do not trigger missing-argument errors.
    if ctx is None:
        try:
            from rpc.jsonrpc import _default_ctx

            ctx = _default_ctx()
        except Exception:
            ctx = None

    res = (
        JSONRPC_DISPATCH(payload, ctx) if ctx is not None else JSONRPC_DISPATCH(payload)
    )

    if asyncio.iscoroutine(res):
        return await t.cast(t.Awaitable[t.Any], res)
    return res


# -----------------------------------------------------------------------------
# WS hub integration (feature-detect)
# -----------------------------------------------------------------------------
WS_ROUTER: t.Optional[APIRouter] = None
WS_HUB: t.Optional[t.Any] = None

# Preferred: router factory and a shared hub
if hasattr(_ws_mod, "get_router") and hasattr(_ws_mod, "hub"):
    try:
        WS_HUB = getattr(_ws_mod, "hub")
        WS_ROUTER = _ws_mod.get_router(WS_HUB)  # type: ignore[assignment]
    except Exception:
        WS_ROUTER = None

# Static router exported
if WS_ROUTER is None and hasattr(_ws_mod, "router"):
    WS_ROUTER = getattr(_ws_mod, "router")

# Last-resort: build a tiny inline WS endpoint around a Hub class
if WS_ROUTER is None:
    router = APIRouter()
    Hub = getattr(_ws_mod, "Hub", None)
    if Hub is not None:
        WS_HUB = Hub()  # type: ignore[call-arg]

        @router.websocket("/ws")
        async def websocket_main(ws: WebSocket) -> None:
            await ws.accept()
            cid = await WS_HUB.join(ws)  # type: ignore[attr-defined]
            try:
                while True:
                    # Echo-protocol / keepalive; the Hub likely broadcasts events elsewhere.
                    _ = await ws.receive_text()
            except WebSocketDisconnect:
                await WS_HUB.leave(cid)  # type: ignore[attr-defined]

        WS_ROUTER = router


# -----------------------------------------------------------------------------
# OpenRPC & Metrics mounting (feature-detect)
# -----------------------------------------------------------------------------
def _mount_openrpc(app: FastAPI) -> None:
    # Supports: mount_openrpc(app) OR get_router()
    if hasattr(_openrpc_mod, "mount_openrpc"):
        _openrpc_mod.mount_openrpc(app)  # type: ignore[misc]
        return
    if hasattr(_openrpc_mod, "get_router"):
        r = _openrpc_mod.get_router()  # type: ignore[call-arg]
        app.include_router(r)
        return

    # Fallback: serve a tiny placeholder (should not happen in this repo)
    @app.get("/openrpc.json")
    def _openrpc_placeholder() -> JSONResponse:
        return JSONResponse(
            {
                "openrpc": "1.2.6",
                "info": {"title": "Animica RPC", "version": rpc_version.__version__},
                "methods": [],
            }
        )


def _mount_metrics(app: FastAPI) -> None:
    # Supports: mount_metrics(app) OR get_router() OR ASGI app
    if hasattr(_metrics_mod, "mount_metrics"):
        _metrics_mod.mount_metrics(app)  # type: ignore[misc]
        return
    if hasattr(_metrics_mod, "get_router"):
        r = _metrics_mod.get_router()  # type: ignore[call-arg]
        app.include_router(r)
        return

    # Fallback handler if prometheus is unavailable
    @app.get("/metrics")
    def _metrics_placeholder() -> PlainTextResponse:
        return PlainTextResponse(
            "# metrics temporarily unavailable\n",
            media_type="text/plain; version=0.0.4",
        )


# -----------------------------------------------------------------------------
# App factory
# -----------------------------------------------------------------------------
def create_app(cfg: rpc_config.Config | None = None) -> FastAPI:
    """
    Build the FastAPI app with:
      - /rpc  (JSON-RPC)
      - /ws   (WebSocket subscriptions)
      - /openrpc.json
      - /metrics
      - /healthz, /readyz, /version
    """
    # Load config
    cfg = cfg or rpc_config.load_config()

    # Basic logging if caller hasn't configured it
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=getattr(logging, cfg.logging.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    app = FastAPI(
        title="Animica JSON-RPC",
        version=rpc_version.__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def _method_not_allowed_hint(
        request: Request, call_next: t.Callable[[Request], t.Awaitable[Response]]
    ):
        if request.url.path.rstrip("/") == "/rpc" and request.method not in {
            "POST",
            "OPTIONS",
        }:
            return JSONResponse(
                {
                    "error": "Method not allowed",
                    "hint": "Send JSON-RPC requests as POST with application/json to /rpc.",
                    "examples": {
                        "single": {
                            "jsonrpc": "2.0",
                            "method": "chain.getChainId",
                            "id": 1,
                        },
                        "withParams": {
                            "jsonrpc": "2.0",
                            "method": "account.getBalance",
                            "params": ["0x1234..."],
                            "id": "balance",
                        },
                        "notification": {"jsonrpc": "2.0", "method": "chain.getHead"},
                        "batch": [
                            {"jsonrpc": "2.0", "method": "chain.getChainId", "id": "a"},
                            {"jsonrpc": "2.0", "method": "chain.getHead", "id": "b"},
                        ],
                    },
                },
                status_code=405,
                headers={"Allow": "POST"},
            )

        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 405 and request.url.path.rstrip("/") == "/rpc":
            return JSONResponse(
                {
                    "error": "Method not allowed",
                    "hint": "Send JSON-RPC requests as POST with application/json to /rpc.",
                    "examples": {
                        "single": {
                            "jsonrpc": "2.0",
                            "method": "chain.getChainId",
                            "id": 1,
                        },
                        "withParams": {
                            "jsonrpc": "2.0",
                            "method": "account.getBalance",
                            "params": ["0x1234..."],
                            "id": "balance",
                        },
                        "notification": {"jsonrpc": "2.0", "method": "chain.getHead"},
                        "batch": [
                            {"jsonrpc": "2.0", "method": "chain.getChainId", "id": "a"},
                            {"jsonrpc": "2.0", "method": "chain.getHead", "id": "b"},
                        ],
                    },
                },
                status_code=exc.status_code,
                headers=exc.headers,
            )

        return await http_exception_handler(request, exc)

    # CORS (strict allowlist)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_allow_origins or [],
        allow_credentials=True,
        allow_methods=["POST", "GET", "OPTIONS"],
        allow_headers=["*"],
        max_age=3600,
    )

    # --- Lifecycle wiring (DBs, heads, pools) ---
    @app.on_event("startup")
    async def _on_startup() -> None:
        log.info(
            "RPC server starting",
            extra={
                "chainId": cfg.chain_id,
                "db": cfg.db_uri,
                "host": cfg.host,
                "port": cfg.port,
            },
        )
        # Initialize deps (idempotent if already set)
        await deps.startup(cfg)

    @app.on_event("shutdown")
    async def _on_shutdown() -> None:
        log.info("RPC server stopping")
        await deps.shutdown()

    # --- Health endpoints ---
    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "version": rpc_version.__version__})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ready, details = await deps.ready()
        status = 200 if ready else 503
        return JSONResponse({"ready": ready, "details": details}, status_code=status)

    @app.get("/version")
    async def version() -> JSONResponse:
        return JSONResponse({"version": rpc_version.__version__})

    # --- JSON-RPC mount ---
    if JSONRPC_ASGI_APP is not None:
        # Mount as sub-app for best performance
        app.mount("/rpc", JSONRPC_ASGI_APP)  # type: ignore[arg-type]
    else:
        # Provide a thin endpoint that forwards to dispatcher
        rpc_router = APIRouter()

        @rpc_router.post("/rpc")
        async def rpc_endpoint(request: Request) -> Response:
            try:
                payload = await request.json()
            except Exception as e:
                err = rpc_errors.ParseError(f"Invalid JSON body: {e}")  # type: ignore[misc]
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": None, "error": err.to_dict()},
                    status_code=200,
                )

            try:
                result = await _call_dispatch(payload, request)
            except rpc_errors.RpcError as re:  # type: ignore[attr-defined]
                # Structured RPC error already; return as-is
                rpc_id = payload.get("id") if isinstance(payload, dict) else None
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": rpc_id, "error": re.to_dict()},
                    status_code=200,
                )
            except Exception as e:
                log.exception("Unhandled error in JSON-RPC")
                # Map to JSON-RPC internal error shape
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "error": {
                            "code": -32603,
                            "message": "Internal error",
                            "data": str(e),
                        },
                        "id": payload.get("id") if isinstance(payload, dict) else None,
                    },
                    status_code=200,
                )

            # If dispatcher returned a native structure, serialize directly
            if result is None:
                return Response(status_code=204)
            if isinstance(result, (dict, list)):
                return JSONResponse(result)
            # As a fallback, dump to JSON
            return Response(content=json.dumps(result), media_type="application/json")

        app.include_router(rpc_router)

    # --- WebSocket subscriptions ---
    if WS_ROUTER is not None:
        app.include_router(WS_ROUTER, prefix="/ws")
    else:
        # Minimal /ws echo (should not be hit if rpc.ws is present)
        @app.websocket("/ws")
        async def _ws_echo(ws: WebSocket) -> None:
            await ws.accept()
            try:
                while True:
                    msg = await ws.receive_text()
                    await ws.send_text(msg)
            except WebSocketDisconnect:
                return

    # --- OpenRPC mount ---
    _mount_openrpc(app)

    # --- Metrics mount ---
    _mount_metrics(app)

    # --- JSON index (optional tiny banner) ---
    @app.get("/")
    async def index() -> JSONResponse:
        return JSONResponse(
            {
                "name": "Animica RPC",
                "version": rpc_version.__version__,
                "endpoints": [
                    "/rpc",
                    "/ws",
                    "/openrpc.json",
                    "/metrics",
                    "/healthz",
                    "/readyz",
                ],
                "chainId": rpc_config.resolve_chain_id(cfg),
            }
        )

    return app


# -----------------------------------------------------------------------------
# Entrypoint (uvicorn)
# -----------------------------------------------------------------------------
def main() -> None:
    cfg = rpc_config.load_config()
    app = create_app(cfg)
    # Lazy import uvicorn so the module is importable in tests without uvicorn installed
    import uvicorn

    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.logging.lower(),
        workers=1,
        # http='h11',  # keep default
    )


if __name__ == "__main__":
    main()
