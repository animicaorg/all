from __future__ import annotations

import typing as t
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _normalize_origin(origin: str) -> str:
    """
    Normalize origins for exact-match comparison:
      - keep scheme
      - hostname in lowercase
      - include port only if explicitly present
      - pass through the special value "null" (for file://, sandboxed docs)
    """
    o = origin.strip()
    if o.lower() == "null":
        return "null"
    p = urlparse(o)
    host = (p.hostname or "").lower()
    if not p.scheme or not host:
        # If it's malformed, keep original to avoid false-positives.
        return o
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme}://{host}{port}"


def _get_cfg_attr(cfg: t.Any, *names: str, default=None):
    for n in names:
        if hasattr(cfg, n):
            return getattr(cfg, n)
    return default


class EnforceOriginMiddleware(BaseHTTPMiddleware):
    """
    Strict origin gate:
      - If no Origin header: allow (non-browser clients).
      - If Origin present and NOT in allowlist: 403 with JSON error.
      - If Origin is "null" and not explicitly allowed: 403 (blocks file:// etc).
    This runs *before* CORS response decoration to fail fast.
    """

    def __init__(self, app, *, allowlist: list[str]) -> None:
        super().__init__(app)
        self.allow = {_normalize_origin(o) for o in allowlist}

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        origin = request.headers.get("origin")
        if not origin:
            return await call_next(request)

        n = _normalize_origin(origin)
        if n not in self.allow:
            # Preflight failures return 403 as well (explicit signal to browser).
            body = {
                "error": {
                    "code": -32006,
                    "message": "CORS origin not allowed",
                    "data": {"origin": origin},
                }
            }
            return JSONResponse(body, status_code=403)

        return await call_next(request)


def mount_strict_cors(app, cfg) -> None:
    """
    Mount a strict CORS policy based on values from rpc.config.RPCConfig.
    It will:
      1) Enforce an explicit allowlist (403 if Origin not allowed).
      2) Decorate responses with precise CORS headers via CORSMiddleware (no wildcards).

    Expected config attributes (with fallbacks):
      - cors_allowlist | cors_origins | cors_allow_origins : list[str]
      - cors_allow_methods | cors_methods                 : list[str] (default ["POST","GET","OPTIONS"])
      - cors_allow_headers | cors_headers                 : list[str] (default ["content-type"])
      - cors_expose_headers | cors_expose                 : list[str] (default ["x-request-id"])
      - cors_allow_credentials                            : bool (default False)
      - cors_max_age                                      : int seconds (default 600)
    """
    allowlist: list[str] = (
        _get_cfg_attr(
            cfg, "cors_allowlist", "cors_origins", "cors_allow_origins", default=[]
        )
        or []
    )

    if not isinstance(allowlist, (list, tuple)) or not allowlist:
        # Safe default: only same-origin dev hosts; adjust in config for prod.
        allowlist = ["http://localhost:5173", "http://127.0.0.1:5173"]

    allow_methods: list[str] = _get_cfg_attr(
        cfg, "cors_allow_methods", "cors_methods", default=["POST", "GET", "OPTIONS"]
    ) or ["POST", "GET", "OPTIONS"]

    allow_headers: list[str] = _get_cfg_attr(
        cfg, "cors_allow_headers", "cors_headers", default=["content-type"]
    ) or ["content-type"]

    expose_headers: list[str] = _get_cfg_attr(
        cfg, "cors_expose_headers", "cors_expose", default=["x-request-id"]
    ) or ["x-request-id"]

    allow_credentials: bool = bool(
        _get_cfg_attr(cfg, "cors_allow_credentials", default=False)
    )
    max_age: int = int(_get_cfg_attr(cfg, "cors_max_age", default=600))

    # IMPORTANT: Starlette middlewares are nested; the *last* added runs first.
    # We want to enforce the Origin gate *before* CORS decoration, so add CORSMiddleware first,
    # then EnforceOriginMiddleware last.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[_normalize_origin(o) for o in allowlist],
        allow_methods=allow_methods,
        allow_headers=allow_headers,
        expose_headers=expose_headers,
        allow_credentials=allow_credentials,
        max_age=max_age,
    )
    app.add_middleware(EnforceOriginMiddleware, allowlist=list(allowlist))


__all__ = ["mount_strict_cors", "EnforceOriginMiddleware"]
