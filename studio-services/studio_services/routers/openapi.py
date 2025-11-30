from __future__ import annotations

"""
OpenAPI & Docs Mount

Mounts an enriched OpenAPI schema and custom docs pages for the Studio Services
API. This module is designed to be called from your app factory:

    from fastapi import FastAPI
    from studio_services.routers.openapi import mount_openapi

    def create_app() -> FastAPI:
        app = FastAPI(
            # You can leave docs_url/openapi_url/redoc_url at their defaults;
            # we override the OpenAPI generator and mount custom UIs below.
            title="Animica Studio Services",
            version=auto_version(),
        )
        # ... mount other routers/middleware ...
        mount_openapi(app)  # <-- important
        return app

It will:
  • Load schema enrichments from studio_services/schemas/openapi_overrides.json (if present)
  • Override app.openapi() to deep-merge those enrichments
  • Mount Swagger UI (/docs) and ReDoc (/redoc) with sensible settings
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional

from fastapi import FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from starlette.responses import HTMLResponse, JSONResponse

log = logging.getLogger(__name__)


# ---------- Utilities ----------


def _deep_merge(
    dst: MutableMapping[str, Any], src: Mapping[str, Any]
) -> MutableMapping[str, Any]:
    """
    Recursively merge src into dst (modifies dst), returning dst.
    Scalars/arrays in src replace dst; dicts are merged.
    """
    for k, v in src.items():
        if isinstance(v, Mapping) and isinstance(dst.get(k), Mapping):
            _deep_merge(dst[k], v)  # type: ignore[index]
        else:
            dst[k] = v
    return dst


def _load_overrides() -> Dict[str, Any]:
    """
    Load OpenAPI overrides if available. Missing file is fine.
    """
    try:
        base = (
            Path(__file__).resolve().parents[1] / "schemas" / "openapi_overrides.json"
        )
        if not base.exists():
            return {}
        with base.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                log.warning("openapi_overrides.json is not an object; ignoring")
                return {}
            return data
    except Exception as e:  # pragma: no cover
        log.warning("failed to load OpenAPI overrides: %s", e)
        return {}


def auto_version() -> str:
    """
    Best-effort version string from studio_services.version, with fallback.
    """
    try:
        from studio_services.version import __version__ as v  # type: ignore

        return str(v)
    except Exception:
        try:
            from studio_services.version import version as vfn  # type: ignore

            return str(vfn())
        except Exception:
            return "0.0.0+dev"


# ---------- Mount ----------


def mount_openapi(
    app: FastAPI,
    *,
    title: Optional[str] = None,
    version: Optional[str] = None,
    description: Optional[str] = None,
    openapi_url: Optional[str] = None,
    docs_url: Optional[str] = None,
    redoc_url: Optional[str] = None,
    favicon_url: Optional[str] = None,
    swagger_ui_parameters: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Override the app's OpenAPI generator with enrichment and (re)mount docs pages.

    NOTE: We don't remove existing routes; we override the OpenAPI generator so
    any existing /openapi.json will serve the enriched schema automatically.
    We also (re)mount docs at the given URLs if they are free.
    """
    overrides = _load_overrides()

    # Wire a custom generator that merges overrides on each build.
    def custom_openapi() -> Dict[str, Any]:
        schema = get_openapi(
            title=title or app.title or "Animica Studio Services",
            version=version or app.version or auto_version(),
            description=description or app.description,
            routes=app.routes,
        )
        if overrides:
            _deep_merge(schema, overrides)
        return schema

    # Patch the app's openapi generator
    app.openapi = custom_openapi  # type: ignore[method-assign]

    # Determine paths (prefer app settings, then our args, then defaults)
    openapi_path = (
        openapi_url or getattr(app, "openapi_url", "/openapi.json") or "/openapi.json"
    )
    docs_path = docs_url or getattr(app, "docs_url", "/docs") or "/docs"
    redoc_path = redoc_url or getattr(app, "redoc_url", "/redoc") or "/redoc"

    # Add an explicit /openapi.json route if one isn't already present. If it is, it will call our patched app.openapi.
    if not any(getattr(r, "path", None) == openapi_path for r in app.router.routes):

        @app.get(openapi_path, include_in_schema=False)
        def _openapi_json() -> JSONResponse:  # type: ignore[no-redef]
            return JSONResponse(app.openapi())

    # Swagger UI (with optional parameters)
    sui_params: Dict[str, Any] = {
        # Friendly defaults; callers can override via swagger_ui_parameters
        "deepLinking": True,
        "displayOperationId": False,
        "tryItOutEnabled": True,
        "syntaxHighlight": {"activated": True, "theme": "nord"},
        "persistAuthorization": False,
    }
    if swagger_ui_parameters:
        sui_params.update(swagger_ui_parameters)

    if not any(getattr(r, "path", None) == docs_path for r in app.router.routes):

        @app.get(docs_path, include_in_schema=False)
        def _swagger_ui() -> HTMLResponse:  # type: ignore[no-redef]
            return get_swagger_ui_html(
                openapi_url=openapi_path,
                title=(title or app.title or "API") + " — Docs",
                swagger_favicon_url=favicon_url,
                swagger_ui_parameters=sui_params,
            )

    # ReDoc
    if not any(getattr(r, "path", None) == redoc_path for r in app.router.routes):

        @app.get(redoc_path, include_in_schema=False)
        def _redoc() -> HTMLResponse:  # type: ignore[no-redef]
            return get_redoc_html(
                openapi_url=openapi_path,
                title=(title or app.title or "API") + " — ReDoc",
                redoc_favicon_url=favicon_url,
            )

    log.info(
        "OpenAPI mounted: openapi=%s docs=%s redoc=%s overrides=%s",
        openapi_path,
        docs_path,
        redoc_path,
        bool(overrides),
    )


__all__ = ["mount_openapi", "auto_version"]
