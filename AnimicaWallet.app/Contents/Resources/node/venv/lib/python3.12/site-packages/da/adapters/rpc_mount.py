from __future__ import annotations

"""
Animica • DA • RPC Mount
========================

Helpers to mount the Data Availability (DA) HTTP/JSON endpoints into the main
FastAPI application used by the node RPC.

Design goals:
- Keep this adapter thin and dependency-light.
- Prefer reusing the canonical router from `da.retrieval.api` if present.
- Provide a robust fallback that wires handlers directly if the API module
  doesn't expose a factory.

Typical usage (inside your RPC server factory)
----------------------------------------------
    from fastapi import FastAPI
    from da.adapters.rpc_mount import mount_da

    def create_app() -> FastAPI:
        app = FastAPI()
        mount_da(app, prefix="")          # mounts under /da/...
        return app

Configuration
-------------
- `prefix`: Optional additional prefix (e.g. "/rpc" to mount under /rpc/da/...).
- `tags`:   OpenAPI tag name(s) for the mounted routes.
- `router_kwargs`: Arbitrary kwargs forwarded to `da.retrieval.api.get_router`
                  if available (e.g., custom auth or rate-limit providers).
"""

from typing import Any, Dict, Iterable, Optional, Sequence

try:
    from fastapi import APIRouter, FastAPI
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "da.adapters.rpc_mount requires FastAPI (pip install fastapi)."
    ) from e


def _get_da_router_via_api_module(**router_kwargs: Any) -> Optional[APIRouter]:
    """
    Try to obtain the canonical DA router from da.retrieval.api.

    Supports multiple shapes for compatibility:
      - get_router(**kwargs) -> APIRouter
      - router (APIRouter instance)
    """
    try:
        import da.retrieval.api as api  # type: ignore
    except Exception:
        return None

    # Preferred: factory function
    if hasattr(api, "get_router") and callable(getattr(api, "get_router")):
        try:
            return api.get_router(**router_kwargs)  # type: ignore[attr-defined]
        except TypeError:
            # Older signature without kwargs
            return api.get_router()  # type: ignore[attr-defined,call-arg]

    # Fallback: module-level router
    if hasattr(api, "router"):
        r = getattr(api, "router")
        if isinstance(r, APIRouter):
            return r

    return None


def _build_fallback_router(**router_kwargs: Any) -> APIRouter:
    """
    Build a minimal DA router directly from handlers if the API module
    doesn't expose a factory. The handlers are expected to be framework-agnostic.
    """
    from da.retrieval.auth import get_auth_dependency  # may return a no-op dep
    from da.retrieval.handlers import (get_blob_handler, get_proof_handler,
                                       post_blob_handler)
    from da.retrieval.rate_limit import \
        get_rate_limiter  # may return a no-op dep

    # Optional dependencies (auth / rate limit) are provided by helper factories.
    auth_dep = get_auth_dependency(**router_kwargs)  # type: ignore[arg-type]
    rate_limit_dep = get_rate_limiter(**router_kwargs)  # type: ignore[arg-type]

    router = APIRouter(prefix="/da", tags=["da"])

    # POST /da/blob
    @router.post("/blob")
    async def _post_blob(
        ns: int,
        filename: Optional[str] = None,
        _auth: Any = auth_dep,  # noqa: B008 (FastAPI dep injection)
        _rl: Any = rate_limit_dep,  # noqa: B008
        body: bytes = b"",  # FastAPI will pass raw body via request.stream in handler
    ):
        # Delegate to handler (expects bytes body and query params)
        return await post_blob_handler(ns=ns, filename=filename, body=body)

    # GET /da/blob/{commitment}
    @router.get("/blob/{commitment}")
    async def _get_blob(
        commitment: str,
        range_header: Optional[str] = None,
        _auth: Any = auth_dep,  # noqa: B008
        _rl: Any = rate_limit_dep,  # noqa: B008
    ):
        return await get_blob_handler(commitment=commitment, range_header=range_header)

    # GET /da/proof
    @router.get("/proof")
    async def _get_proof(
        commitment: str,
        samples: str,
        _auth: Any = auth_dep,  # noqa: B008
        _rl: Any = rate_limit_dep,  # noqa: B008
    ):
        # samples="1,5,9"
        return await get_proof_handler(commitment=commitment, samples=samples)

    return router


def mount_da(
    app: FastAPI,
    *,
    prefix: str = "",
    tags: Sequence[str] = ("da",),
    **router_kwargs: Any,
) -> APIRouter:
    """
    Mount DA routes into the given FastAPI app. Returns the mounted APIRouter.
    """
    # Try canonical API router first
    router = _get_da_router_via_api_module(**router_kwargs)
    if router is None:
        # Build a small fallback that calls the handlers directly.
        router = _build_fallback_router(**router_kwargs)

    # Ensure tags if caller wants to override (when using canonical router).
    if (
        tags
        and hasattr(router, "tags")
        and not set(tags).issubset(set(router.tags or []))
    ):
        # This is a benign difference in OpenAPI tagging and doesn't affect routing;
        # we won't mutate router.tags to avoid surprising side-effects. Users can
        # set tags via the canonical get_router if they need stricter control.
        pass

    mount_prefix = prefix.rstrip("/")
    app.include_router(router, prefix=mount_prefix)
    return router


__all__ = ["mount_da"]
