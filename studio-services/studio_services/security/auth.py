from __future__ import annotations

"""
API-key auth (Bearer/query/header) + Origin allowlist helpers.

- Accepts API keys from:
    * Authorization: Bearer <token>
    * X-API-Key: <token>
    * ?api_key=<token> (query)

- Keys are configured via env:
    * STUDIO_API_KEYS="key1,key2,..." (comma or newline separated)
      (aliases checked as fallback: API_KEYS)

- Origin allowlist (CORS hardening):
    * Use `setup_cors()` to install CORSMiddleware based on env:
        - CORS_ALLOW_ORIGINS: "https://app.example.com,https://studio.example"
          (supports "*" to allow all — discouraged in prod)
        - CORS_ALLOW_CREDENTIALS: "1" to enable credentials
    * Optional dependency `RequireAllowedOrigin` rejects requests with an Origin
      header not in the allowlist (defense-in-depth in addition to CORS).

Usage
-----
    from fastapi import Depends, FastAPI
    from studio_services.security.auth import ApiKeyAuth, setup_cors

    app = FastAPI()
    setup_cors(app)

    require_key = ApiKeyAuth(required=True)

    @app.get("/admin")
    async def admin(_auth=Depends(require_key)):
        return {"ok": True}
"""

import hmac
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

# ------------------------------ config helpers --------------------------------


def _split_env_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    # Support commas or newlines
    vals = []
    for piece in value.replace("\r", "\n").split("\n"):
        for item in piece.split(","):
            s = item.strip()
            if s:
                vals.append(s)
    return vals


def load_api_keys_from_env() -> Set[str]:
    """
    Load allowed API keys from environment. Empty set if none configured.
    """
    raw = os.getenv("STUDIO_API_KEYS") or os.getenv("API_KEYS") or ""
    return set(_split_env_list(raw))


def load_allowed_origins_from_env() -> List[str]:
    """
    Load allowed origins for CORS from environment.
    """
    raw = os.getenv("CORS_ALLOW_ORIGINS") or os.getenv("ALLOWED_ORIGINS") or ""
    vals = _split_env_list(raw)
    # Normalize trailing slashes
    return [v.rstrip("/") for v in vals]


def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ------------------------------ API Key auth ----------------------------------


@dataclass(frozen=True)
class ApiKeySources:
    header_auth: str = "authorization"
    header_api_key: str = "x-api-key"
    query_param: str = "api_key"


def _extract_bearer(token_hdr: str) -> Optional[str]:
    parts = token_hdr.split()
    if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
        return parts[1].strip()
    return None


class ApiKeyAuth:
    """
    FastAPI dependency that validates an API key from Authorization/X-API-Key/query.

    - If `required=True` and no valid key is provided, raises HTTP 401.
    - If `required=False` and no key is provided, passes and returns None.
      (Useful for endpoints that are public but optionally authenticated.)

    The instance is callable and can be used with `Depends(ApiKeyAuth(...))`.
    """

    def __init__(
        self,
        *,
        required: bool = True,
        valid_keys: Optional[Iterable[str]] = None,
        sources: ApiKeySources = ApiKeySources(),
        realm: str = "api",
    ) -> None:
        self.required = required
        keys = set(valid_keys) if valid_keys is not None else load_api_keys_from_env()
        self.valid_keys: Set[str] = keys
        self.sources = sources
        self.realm = realm

        if self.required and not self.valid_keys:
            # Fail fast: secure-by-default for protected routes.
            # If you need to run without a key, set required=False explicitly.
            raise RuntimeError(
                "ApiKeyAuth(required=True) but no API keys configured. "
                "Set STUDIO_API_KEYS or pass `valid_keys=[...]`."
            )

    async def __call__(self, request: Request) -> Optional[str]:
        token = await self._get_token_from_request(request)
        if token is None:
            if self.required:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing API key",
                    headers={"WWW-Authenticate": f'Bearer realm="{self.realm}"'},
                )
            return None

        if not self._is_valid(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": f'Bearer realm="{self.realm}"'},
            )
        return token

    async def _get_token_from_request(self, request: Request) -> Optional[str]:
        # 1) Authorization: Bearer <token>
        auth = request.headers.get(self.sources.header_auth)
        if auth:
            bearer = _extract_bearer(auth)
            if bearer:
                return bearer

        # 2) X-API-Key: <token>
        hdr = request.headers.get(self.sources.header_api_key)
        if hdr:
            return hdr.strip()

        # 3) ?api_key=<token>
        qp = request.query_params.get(self.sources.query_param)
        if qp:
            return qp.strip()

        return None

    def _is_valid(self, token: str) -> bool:
        # Constant-time compare against every configured key (small set expected).
        for k in self.valid_keys:
            if hmac.compare_digest(token, k):
                return True
        return False


# Ready-made singleton for routes that MUST be protected via API key.
def require_api_key() -> Depends:
    """
    Convenience factory for Depends() with required=True using env keys.
    Call once at import time in your router/module:

        from fastapi import Depends
        from studio_services.security.auth import require_api_key

        @router.post("/faucet/drip")
        def drip(_auth = require_api_key()):
            ...
    """
    return Depends(ApiKeyAuth(required=True))


# ------------------------------ Origin allowlist ------------------------------


class RequireAllowedOrigin:
    """
    Optional FastAPI dependency to enforce requests with an Origin header match
    the configured allowlist. (This is in addition to CORS middleware.)

    - If no Origin header is present, the request is allowed (typical for curl/server-to-server).
    - If any allowed origin is "*", all origins are accepted.
    """

    def __init__(self, allowed_origins: Optional[Sequence[str]] = None) -> None:
        origins = (
            list(allowed_origins)
            if allowed_origins is not None
            else load_allowed_origins_from_env()
        )
        self.allowed: List[str] = [o.rstrip("/") for o in origins]

    async def __call__(self, request: Request) -> None:
        origin = (request.headers.get("origin") or "").rstrip("/")
        if not origin:
            # No browser-origin context; allow.
            return
        if "*" in self.allowed:
            return
        if origin in self.allowed:
            return
        # Some proxies strip trailing slash or scheme nuances; allow http->https upgrade match if configured.
        if (
            origin.startswith("http://")
            and origin.replace("http://", "https://", 1) in self.allowed
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed"
        )


def setup_cors(app: FastAPI) -> None:
    """
    Install Starlette CORSMiddleware based on environment:

    - CORS_ALLOW_ORIGINS: CSV of origins or "*" (default: empty → no cross-origin)
    - CORS_ALLOW_CREDENTIALS: "1" to allow credentials (default: 0)
    - CORS_ALLOW_METHODS: CSV of methods (default: GET,POST,PUT,PATCH,DELETE,OPTIONS)
    - CORS_ALLOW_HEADERS: CSV of headers (default: * )
    - CORS_EXPOSE_HEADERS: CSV of headers to expose (default: none)
    """

    allow_origins = load_allowed_origins_from_env()
    allow_credentials = _bool_env("CORS_ALLOW_CREDENTIALS", default=False)

    methods = _split_env_list(os.getenv("CORS_ALLOW_METHODS")) or [
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ]
    headers = _split_env_list(os.getenv("CORS_ALLOW_HEADERS")) or ["*"]
    expose = _split_env_list(os.getenv("CORS_EXPOSE_HEADERS")) or []

    # Note: Starlette prohibits allow_origins=["*"] when allow_credentials=True.
    if allow_credentials and allow_origins == ["*"]:
        raise RuntimeError(
            "CORS_ALLOW_CREDENTIALS=1 cannot be used with CORS_ALLOW_ORIGINS='*'"
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or [],  # empty → deny cross-origin
        allow_methods=methods,
        allow_headers=headers,
        allow_credentials=allow_credentials,
        expose_headers=expose,
        max_age=3600,
    )


__all__ = [
    "ApiKeyAuth",
    "ApiKeySources",
    "RequireAllowedOrigin",
    "require_api_key",
    "setup_cors",
    "load_api_keys_from_env",
    "load_allowed_origins_from_env",
]
