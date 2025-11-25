from __future__ import annotations

"""
Animica • DA • Retrieval • Auth

Optional API-key auth with simple "tiers" suited for faucet/testnet usage.

Behavior
--------
- By default, endpoints are OPEN (no token required). This is controlled via
  environment variable DA_REQUIRE_AUTH (default: "false").
- If a token is provided (via Authorization: Bearer, or query ?api_key=),
  we look it up in a TokenStore and attach an AuthContext to request.state.auth.
- If DA_REQUIRE_AUTH=true and the token is missing or invalid, raise 401.

Token sources
-------------
1) DA_AUTH_TOKENS_JSON      : JSON object mapping token -> { "tier": "test", "subject": "alice" }
2) DA_AUTH_TOKEN_FILE       : newline-delimited "token[,tier[,subject]]"; '#' comments allowed.
3) Both may be provided; JSON overrides file entries with the same token.

Tiers (suggested)
-----------------
- public   : default when no token present (when auth not required)
- test     : faucet/testnet-friendly; low rate limits
- provider : for DA or compute providers; higher limits
- admin    : administrative endpoints (if any)

Exports
-------
- AuthContext            : dataclass with token/tier/subject
- TokenStore             : loader and in-memory verifier
- auth_dependency(req)   : FastAPI dependency; sets req.state.auth and returns AuthContext
- token_from_request(req): utility to extract bearer/query token
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status

# ----------------------------- Model -----------------------------

@dataclass(frozen=True)
class AuthContext:
    token: Optional[str]
    tier: str
    subject: Optional[str] = None

    @property
    def is_authenticated(self) -> bool:
        return self.token is not None


# --------------------------- TokenStore --------------------------

class TokenStore:
    """
    In-memory token store with two loaders:
      - from DA_AUTH_TOKEN_FILE (line format: token[,tier[,subject]])
      - from DA_AUTH_TOKENS_JSON (object: token -> {tier, subject})
    """

    def __init__(self, mapping: Optional[Dict[str, Tuple[str, Optional[str]]]] = None):
        # token -> (tier, subject)
        self._map: Dict[str, Tuple[str, Optional[str]]] = mapping or {}

    @staticmethod
    def _normalize_tier(tier: Optional[str]) -> str:
        t = (tier or "test").strip().lower()
        return t if t in {"public", "test", "provider", "admin"} else "test"

    @classmethod
    def from_environ(cls) -> "TokenStore":
        mapping: Dict[str, Tuple[str, Optional[str]]] = {}

        # File source
        file_path = os.getenv("DA_AUTH_TOKEN_FILE")
        if file_path and os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    # token[,tier[,subject]]
                    parts = [p.strip() for p in line.split(",")]
                    if not parts or not parts[0]:
                        continue
                    token = parts[0]
                    tier = cls._normalize_tier(parts[1] if len(parts) > 1 else None)
                    subject = parts[2] if len(parts) > 2 and parts[2] else None
                    mapping[token] = (tier, subject)

        # JSON source (overrides)
        raw_json = os.getenv("DA_AUTH_TOKENS_JSON")
        if raw_json:
            try:
                obj = json.loads(raw_json)
                if isinstance(obj, dict):
                    for token, meta in obj.items():
                        if not token:
                            continue
                        if isinstance(meta, dict):
                            tier = cls._normalize_tier(meta.get("tier"))
                            subject = meta.get("subject")
                        else:
                            tier = "test"
                            subject = None
                        mapping[token] = (tier, subject)
            except Exception:
                # If JSON fails, keep what we had from file — don't crash server.
                pass

        return cls(mapping=mapping)

    def check(self, token: Optional[str]) -> Optional[AuthContext]:
        if not token:
            return None
        row = self._map.get(token)
        if row is None:
            return None
        tier, subject = row
        return AuthContext(token=token, tier=tier, subject=subject)


# Singleton store (loaded at import; safe to reload manually if env changes)
_TOKEN_STORE = TokenStore.from_environ()


# ------------------------ Request utilities ----------------------

def token_from_request(request: Request) -> Optional[str]:
    """
    Extract token from:
      - Authorization: Bearer <token>
      - ?api_key=<token> (or ?token= / ?key= for convenience)
    """
    # Header
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and isinstance(auth, str):
        parts = auth.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()

    # Query params
    qp = request.query_params
    for k in ("api_key", "token", "key"):
        if k in qp and qp.get(k):
            return qp.get(k)

    return None


# ------------------------ FastAPI dependency ---------------------

def auth_dependency(request: Request) -> AuthContext:
    """
    Resolve the AuthContext for the current request and attach it to request.state.auth.

    Env:
      DA_REQUIRE_AUTH = "true" | "false" (default: "false")
      DA_DEFAULT_TIER = default tier when no token is supplied (default: "public")
    """
    require_auth = os.getenv("DA_REQUIRE_AUTH", "false").strip().lower() == "true"
    default_tier = os.getenv("DA_DEFAULT_TIER", "public").strip().lower() or "public"

    token = token_from_request(request)
    ctx = _TOKEN_STORE.check(token)

    if ctx is None:
        if require_auth:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid API key",
                headers={"WWW-Authenticate": 'Bearer realm="Animica-DA"'},
            )
        # Anonymous / public
        ctx = AuthContext(token=None, tier=default_tier, subject=None)

    # Attach to request for downstream middlewares/handlers
    try:
        request.state.auth = ctx  # type: ignore[attr-defined]
    except Exception:
        pass

    return ctx


# --------------------------- Re-export ---------------------------

__all__ = [
    "AuthContext",
    "TokenStore",
    "auth_dependency",
    "token_from_request",
]
