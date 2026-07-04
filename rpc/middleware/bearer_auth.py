"""
Bearer-token authentication for the JSON-RPC HTTP endpoints (ANM-C08).

Design goals (see 6.0.0 hardening constraints):

* SAFE DEFAULT — when no token is configured the middleware is a pass-through,
  so the live pool / console / explorer keep calling the node without a token.
  The server logs one prominent startup warning in that case (see
  ``rpc.server.create_app``).
* FAIL-CLOSED when enabled — when ``ANIMICA_RPC_AUTH_TOKEN`` (surfaced on the
  config as ``auth_token``) is set, every JSON-RPC POST to ``/`` or ``/rpc``
  must present a matching token via ``Authorization: Bearer <token>`` or the
  ``X-Animica-Auth-Token`` header. Missing/incorrect tokens get a clean 401
  with a JSON-RPC-shaped error body and no stack traces.

Only the JSON-RPC POST surface is gated. Health/metrics/openrpc/version and the
GET banner stay open so liveness probes and discovery keep working. CORS
preflight (OPTIONS) is never gated. WebSocket connections bypass
``BaseHTTPMiddleware`` entirely and are out of scope for this control.
"""

from __future__ import annotations

import hmac
import typing as t

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# JSON-RPC "Access denied" code (mirrors rpc.errors.AnimicaCode.ACCESS_DENIED)
_ACCESS_DENIED = -32003

# JSON-RPC POST paths that must be authenticated when a token is configured.
_DEFAULT_PROTECTED = ("/", "/rpc")


def _extract_token(request: Request) -> t.Optional[str]:
    """Return the presented bearer token, or None."""
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            tok = parts[1].strip()
            if tok:
                return tok
    header_tok = request.headers.get("x-animica-auth-token")
    if header_tok:
        header_tok = header_tok.strip()
        if header_tok:
            return header_tok
    return None


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require a bearer token on the JSON-RPC POST endpoints when configured."""

    def __init__(
        self,
        app,
        *,
        token: t.Optional[str] = None,
        protected_paths: t.Iterable[str] = _DEFAULT_PROTECTED,
    ) -> None:
        super().__init__(app)
        self.token = (token or "").strip() or None
        self.protected = {p.rstrip("/") or "/" for p in protected_paths}

    def _is_protected(self, path: str) -> bool:
        norm = path.rstrip("/") or "/"
        if norm in self.protected:
            return True
        # cover mounted sub-paths like /rpc/... as well
        return norm.startswith("/rpc/")

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Disabled (no token) → preserve current open behavior.
        if self.token is None:
            return await call_next(request)

        # Only gate the JSON-RPC POST surface; never gate CORS preflight or
        # non-HTTP scopes.
        if request.scope.get("type") != "http" or request.method != "POST":
            return await call_next(request)

        if not self._is_protected(request.url.path):
            return await call_next(request)

        presented = _extract_token(request)
        if presented is None or not hmac.compare_digest(presented, self.token):
            return self._unauthorized()

        return await call_next(request)

    def _unauthorized(self) -> Response:
        body = {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": _ACCESS_DENIED,
                "message": "Unauthorized",
                "data": {
                    "reason": "missing or invalid RPC auth token",
                    "hint": (
                        "Send 'Authorization: Bearer <token>' or "
                        "'X-Animica-Auth-Token: <token>'"
                    ),
                },
            },
        }
        return JSONResponse(
            body,
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )


__all__ = ["BearerAuthMiddleware"]
