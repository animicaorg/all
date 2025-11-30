from __future__ import annotations

"""
Strict CORS configuration helpers for FastAPI.

Design goals
------------
- **Deny by default.** No origins are allowed unless explicitly configured.
- Support exact-origin allowlist *and* safe wildcard patterns (e.g. "https://*.example.com").
- Reject insecure combinations (e.g. "*" with credentials enabled).
- Sensible defaults for methods/headers and short `max_age` for preflight caching.

Environment variables
---------------------
- CORS_ALLOW_ORIGINS
    Comma-separated list of exact origins (e.g. "https://app.example.com,https://studio.example.org")
    and/or glob-style patterns (e.g. "https://*.example.com").
    Use "*" to allow all origins (only valid when CORS_ALLOW_CREDENTIALS=false).
- CORS_ALLOW_METHODS
    Comma-separated list; default: "GET,POST,OPTIONS".
- CORS_ALLOW_HEADERS
    Comma-separated list; default: "Authorization,Content-Type".
- CORS_EXPOSE_HEADERS
    Comma-separated list; default: "X-Request-Id,X-RateLimit-Limit,X-RateLimit-Remaining,X-RateLimit-Reason".
- CORS_ALLOW_CREDENTIALS
    "true" or "false"; default: "false".
- CORS_MAX_AGE
    Seconds to cache preflight; default: "600".
- CORS_DEBUG
    "true" to log the resolved config at startup.

Usage
-----
    from fastapi import FastAPI
    from studio_services.security.cors import setup_cors

    app = FastAPI()
    setup_cors(app)  # reads env and installs CORSMiddleware

You can also pass an explicit CORSConfig for tests or custom bootstraps.
"""

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware


@dataclass(frozen=True)
class CORSConfig:
    allow_origins: List[str]
    allow_origin_regex: Optional[str]  # single combined regex or None
    allow_methods: List[str]
    allow_headers: List[str]
    expose_headers: List[str]
    allow_credentials: bool
    max_age: int
    debug: bool = False


# ------------------------- helpers: parsing & validation -------------------------


def _split_csv(val: str | None) -> List[str]:
    if not val:
        return []
    return [x.strip() for x in val.split(",") if x.strip()]


def _as_bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


_GLOB_CHARS = re.compile(r"[.*?]")  # quick check if pattern contains globbing


def _glob_to_regex(glob_origin: str) -> str:
    """
    Convert a limited glob origin like "https://*.example.com" to a safe anchored regex.
    Only '*' is supported as a wildcard for a single label segment (no path parts allowed).
    """
    # Basic sanity: must look like scheme://host[:port]
    if "://" not in glob_origin:
        raise ValueError(f"Invalid origin pattern (missing scheme): {glob_origin!r}")
    scheme, rest = glob_origin.split("://", 1)
    if "/" in rest:
        raise ValueError(f"Origin patterns must not include paths: {glob_origin!r}")

    # Escape dots and other regex metacharacters, then replace '*' with a safe subpattern.
    # We allow wildcard only within the hostname label portion.
    escaped = re.escape(glob_origin)
    # Convert the escaped "\*" back to regex that matches a valid DNS label segment(s).
    # Here we allow any non-slash characters until the next dot, repeatedly (subdomains).
    # Example: https://*.example.com -> ^https://(?:[^/.:]+\.)+example\.com$
    escaped = escaped.replace(r"\*", r"(?:[^/.:]+\.)+")
    return r"^" + escaped + r"$"


def _combine_regexes(regexes: List[str]) -> Optional[str]:
    if not regexes:
        return None
    if len(regexes) == 1:
        return regexes[0]
    return r"^(?:" + r"|".join(regexes) + r")$"


def load_cors_config_from_env() -> CORSConfig:
    allow_origins_raw = _split_csv(os.getenv("CORS_ALLOW_ORIGINS"))
    allow_methods = _split_csv(os.getenv("CORS_ALLOW_METHODS")) or [
        "GET",
        "POST",
        "OPTIONS",
    ]
    allow_headers = _split_csv(os.getenv("CORS_ALLOW_HEADERS")) or [
        "Authorization",
        "Content-Type",
    ]
    expose_headers = _split_csv(os.getenv("CORS_EXPOSE_HEADERS")) or [
        "X-Request-Id",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reason",
    ]
    allow_credentials = _as_bool(os.getenv("CORS_ALLOW_CREDENTIALS"), default=False)
    max_age_str = os.getenv("CORS_MAX_AGE") or "600"
    debug = _as_bool(os.getenv("CORS_DEBUG"), default=False)

    try:
        max_age = max(0, int(max_age_str))
    except ValueError:
        raise ValueError(f"Invalid CORS_MAX_AGE: {max_age_str!r}")

    # Special-case wildcard "*"
    if allow_origins_raw == ["*"]:
        if allow_credentials:
            raise ValueError(
                'CORS_ALLOW_ORIGINS="*" is incompatible with CORS_ALLOW_CREDENTIALS=true'
            )
        # starlette will treat allow_origins=["*"] correctly (no credentials)
        return CORSConfig(
            allow_origins=["*"],
            allow_origin_regex=None,
            allow_methods=allow_methods,
            allow_headers=allow_headers,
            expose_headers=expose_headers,
            allow_credentials=False,
            max_age=max_age,
            debug=debug,
        )

    exact: List[str] = []
    regexes: List[str] = []

    for origin in allow_origins_raw:
        if not _GLOB_CHARS.search(origin):
            # Exact origin
            exact.append(origin)
        else:
            # Glob pattern -> regex
            regexes.append(_glob_to_regex(origin))

    combined_regex = _combine_regexes(regexes)

    return CORSConfig(
        allow_origins=exact,
        allow_origin_regex=combined_regex,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
        expose_headers=expose_headers,
        allow_credentials=allow_credentials,
        max_age=max_age,
        debug=debug,
    )


# ---------------------------------- install -------------------------------------


def setup_cors(app: FastAPI, config: Optional[CORSConfig] = None) -> CORSConfig:
    """
    Attach a strict CORSMiddleware to `app`.

    If `config` is None, reads from environment via `load_cors_config_from_env()`.
    """
    cfg = config or load_cors_config_from_env()

    if cfg.debug:
        try:
            import logging

            logging.getLogger(__name__).info(
                "CORS config",
                extra={
                    "allow_origins": cfg.allow_origins,
                    "allow_origin_regex": cfg.allow_origin_regex,
                    "allow_methods": cfg.allow_methods,
                    "allow_headers": cfg.allow_headers,
                    "expose_headers": cfg.expose_headers,
                    "allow_credentials": cfg.allow_credentials,
                    "max_age": cfg.max_age,
                },
            )
        except Exception:
            pass

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.allow_origins,
        allow_origin_regex=cfg.allow_origin_regex,
        allow_credentials=cfg.allow_credentials,
        allow_methods=cfg.allow_methods,
        allow_headers=cfg.allow_headers,
        expose_headers=cfg.expose_headers,
        max_age=cfg.max_age,
    )
    return cfg


__all__ = [
    "CORSConfig",
    "setup_cors",
    "load_cors_config_from_env",
]
