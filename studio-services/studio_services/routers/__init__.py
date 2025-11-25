"""
Routers package: aggregates all HTTP routes into a single APIRouter.

This module is intentionally resilient: each sub-router (health, deploy, verify,
faucet, artifacts, simulate, openapi) is imported dynamically. If an optional
router is missing (e.g., faucet disabled in this build), it is skipped cleanly.

Usage (from app factory):
    from studio_services.routers import build_router
    app.include_router(build_router())
"""

from __future__ import annotations

import importlib
import logging
from typing import Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter

log = logging.getLogger(__name__)

# Candidate router modules. Order controls route declaration order and OpenAPI grouping.
ROUTER_MODULES: Tuple[str, ...] = (
    "studio_services.routers.health",
    "studio_services.routers.deploy",
    "studio_services.routers.verify",
    "studio_services.routers.artifacts",
    "studio_services.routers.simulate",
    # Optional / environment-gated
    "studio_services.routers.faucet",
    # Keep OpenAPI last so its mounts don't shadow other routes
    "studio_services.routers.openapi",
)


def _load_router(module_path: str) -> Optional[APIRouter]:
    """
    Import a module and return an APIRouter instance.

    The module may expose one of:
      - `router: APIRouter`
      - `get_router() -> APIRouter`
    """
    try:
        mod = importlib.import_module(module_path)
    except Exception as e:
        log.info("router module skipped (import failed): %s (%s)", module_path, e)
        return None

    # Preferred: explicit `router`
    router = getattr(mod, "router", None)
    if isinstance(router, APIRouter):
        return router

    # Fallback: factory function
    get_router = getattr(mod, "get_router", None)
    if callable(get_router):
        try:
            r = get_router()
            if isinstance(r, APIRouter):
                return r
        except Exception as e:
            log.warning("get_router() failed for %s: %s", module_path, e)

    log.warning("module %s has no APIRouter export; skipping", module_path)
    return None


def collect_routers(candidates: Sequence[str] = ROUTER_MODULES) -> List[APIRouter]:
    """Load all available routers in order."""
    routers: List[APIRouter] = []
    for mod in candidates:
        r = _load_router(mod)
        if r is not None:
            routers.append(r)
            log.debug("mounted router from %s (prefixes=%s, tags=%s)", mod, getattr(r, "prefix", ""), getattr(r, "tags", []))
    return routers


def build_router(extra_modules: Optional[Iterable[str]] = None) -> APIRouter:
    """
    Build a single top-level APIRouter that includes all discovered sub-routers.

    Args:
        extra_modules: Optional sequence of additional module paths to attempt loading.

    Returns:
        APIRouter with all included routes.
    """
    root = APIRouter()
    modules: List[str] = list(ROUTER_MODULES)
    if extra_modules:
        modules.extend(extra_modules)

    for r in collect_routers(modules):
        root.include_router(r)

    return root


__all__ = [
    "ROUTER_MODULES",
    "build_router",
    "collect_routers",
]
