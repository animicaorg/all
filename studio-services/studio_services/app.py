from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI

# Local modules
from .version import __version__
from .config import Config, load_config
from .metrics import setup_metrics
from .middleware.request_id import RequestIdMiddleware
from .middleware.logging import install_access_log_middleware
from .middleware.errors import install_error_handlers
from .security.cors import setup_cors
from .security.rate_limit import RateLimitMiddleware, RateLimiter

# Routers
from .routers.health import router as health_router
from .routers.deploy import router as deploy_router
from .routers.verify import router as verify_router
from .routers.faucet import router as faucet_router
from .routers.artifacts import router as artifacts_router
from .routers.simulate import router as simulate_router
from .routers.openapi import mount_openapi

# Storage & background tasks (best-effort imports; components may be optional)
from .storage import sqlite as storage_sqlite
from .tasks.scheduler import Scheduler


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    App lifespan: open DB, start background workers, then gracefully shut down.
    """
    cfg: Config = app.state.config

    # Initialize/migrate DB (idempotent)
    db = await storage_sqlite.init_db(cfg) if hasattr(storage_sqlite, "init_db") else None
    if db is None and hasattr(storage_sqlite, "open_db"):
        # older helper name
        open_db = getattr(storage_sqlite, "open_db")
        maybe = open_db(cfg)  # may be sync
        db = await maybe if asyncio.iscoroutine(maybe) else maybe

    app.state.db = db

    # Start background scheduler (for verification queue, faucet pacing, etc.)
    scheduler: Optional[Scheduler] = None
    if hasattr(Scheduler, "__call__") or hasattr(Scheduler, "start"):
        scheduler = Scheduler(app)
        await scheduler.start()
    app.state.scheduler = scheduler

    try:
        yield
    finally:
        # Stop scheduler
        if scheduler is not None:
            await scheduler.stop()

        # Close DB
        if db is not None:
            if hasattr(storage_sqlite, "close_db"):
                close_db = getattr(storage_sqlite, "close_db")
                maybe_close = close_db(db)
                if asyncio.iscoroutine(maybe_close):
                    await maybe_close
            elif hasattr(db, "close"):
                maybe_close = db.close()
                if asyncio.iscoroutine(maybe_close):
                    await maybe_close


def create_app(config: Optional[Config] = None) -> FastAPI:
    """
    FastAPI factory. Mounts routers, middleware, metrics, and OpenAPI.
    """
    cfg = config or load_config()

    app = FastAPI(
        title="Animica Studio Services",
        version=__version__,
        docs_url=None,      # served via custom OpenAPI mount
        redoc_url=None,
        openapi_url=None,   # hidden; mount at /openapi.json via router
        lifespan=_lifespan,
    )
    app.state.config = cfg

    # Core middleware stack
    app.add_middleware(RequestIdMiddleware)
    # Rate limiter (global + per-route buckets configured in Config)
    limiter_cfg = cfg.to_rate_config() if hasattr(cfg, "to_rate_config") else None
    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(config=limiter_cfg))

    # Access logging after rate-limit so rejected requests are visible with status
    install_access_log_middleware(app)

    # CORS (strict allowlist from config)
    cors_cfg = cfg.to_cors_config() if hasattr(cfg, "to_cors_config") else None
    setup_cors(app, config=cors_cfg)

    # Error → JSON problem+status mapping
    install_error_handlers(app)

    # Metrics (/metrics)
    setup_metrics(app)

    # Routers
    app.include_router(health_router, prefix="")
    app.include_router(deploy_router, prefix="")
    app.include_router(verify_router, prefix="")
    app.include_router(artifacts_router, prefix="")
    app.include_router(simulate_router, prefix="")
    # Faucet endpoints may be disabled via config; router enforces at runtime as well
    app.include_router(faucet_router, prefix="")

    # OpenAPI + docs mount (pretty docs + enriched schema)
    mount_openapi(app)

    return app


# Convenience entrypoint for `uvicorn studio_services.app:app`
app = create_app()
