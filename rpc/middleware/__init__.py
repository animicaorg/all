"""
Animica RPC — Middleware wiring
===============================

This package exposes a single convenience helper, `apply_middleware(app, config)`,
which installs our standard middleware stack (structured request logging, token-
bucket rate limiting, and strict CORS) onto a FastAPI application.

The function is intentionally *lazy-importing* submodules to avoid import-order
pitfalls during bring-up. It also "duck-types" the provided `config` so it works
with `rpc.config.Config` as well as simple dict-like objects in tests.

Expected config attributes (if present; all have safe defaults):
- logging:  .enabled: bool = True
            .request_body_sample: int = 0
- rate_limit / rate_limits:
            .enabled: bool = True
            .capacity: int = 100
            .refill_rate_per_sec: float = 50.0
            .burst: int = 50
            .per_ip: bool = True
            .per_method: bool = True
- cors:
            .allow_origins: list[str] = ["*"]
            .allow_methods: list[str] = ["POST", "OPTIONS"]
            .allow_headers: list[str] = ["content-type"]
            .allow_credentials: bool = False

Order of installation:
1) Logging → captures timings and status for all downstream middleware.
2) RateLimit → rejects excess requests early with JSON-RPC–shaped errors.
3) CORS → last, so preflight/headers are added even for rejections above.

You can also import concrete middleware classes directly:
    from rpc.middleware import LoggingMiddleware, RateLimitMiddleware, StrictCORSMiddleware
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

__all__ = [
    "apply_middleware",
    "LoggingMiddleware",
    "RateLimitMiddleware",
    "StrictCORSMiddleware",
]

if TYPE_CHECKING:
    # FastAPI types are optional at import time (kept under TYPE_CHECKING)
    from fastapi import FastAPI  # pragma: no cover


# Re-export class names for convenience (bound lazily at runtime in apply_middleware)
def __getattr__(name: str):  # pragma: no cover - thin shim
    if name == "LoggingMiddleware":
        from .logging import LoggingMiddleware
        return LoggingMiddleware
    if name == "RateLimitMiddleware":
        from .rate_limit import RateLimitMiddleware
        return RateLimitMiddleware
    if name == "StrictCORSMiddleware":
        from .cors import StrictCORSMiddleware
        return StrictCORSMiddleware
    raise AttributeError(name)


def _get(cfg: Any, *path: str, default: Any = None) -> Any:
    """
    Safe nested getter that supports both attribute and dict access.
    """
    cur = cfg
    for key in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(key, None)
        else:
            cur = getattr(cur, key, None)
    return default if cur is None else cur


def _extract_rate_limits(cfg: Any) -> Dict[str, Any]:
    rl = _get(cfg, "rate_limit") or _get(cfg, "rate_limits") or {}
    return {
        "enabled": bool(_get(rl, "enabled", default=True)),
        "capacity": int(_get(rl, "capacity", default=100)),
        "refill_rate_per_sec": float(_get(rl, "refill_rate_per_sec", default=50.0)),
        "burst": int(_get(rl, "burst", default=50)),
        "per_ip": bool(_get(rl, "per_ip", default=True)),
        "per_method": bool(_get(rl, "per_method", default=True)),
    }


def _extract_logging(cfg: Any) -> Dict[str, Any]:
    lg = _get(cfg, "logging") or {}
    return {
        "enabled": bool(_get(lg, "enabled", default=True)),
        "request_body_sample": int(_get(lg, "request_body_sample", default=0)),
    }


def _extract_cors(cfg: Any) -> Dict[str, Any]:
    cors = _get(cfg, "cors") or {}
    return {
        "allow_origins": list(_get(cors, "allow_origins", default=["*"])),
        "allow_methods": list(_get(cors, "allow_methods", default=["POST", "OPTIONS"])),
        "allow_headers": list(_get(cors, "allow_headers", default=["content-type"])),
        "allow_credentials": bool(_get(cors, "allow_credentials", default=False)),
    }


def apply_middleware(app: "FastAPI", config: Any) -> None:
    """
    Install the standard middleware stack on a FastAPI app.

    Example
    -------
    >>> from fastapi import FastAPI
    >>> from rpc.middleware import apply_middleware
    >>> from rpc.config import Config
    >>> app = FastAPI()
    >>> cfg = Config.from_env()
    >>> apply_middleware(app, cfg)
    """
    # Lazy imports to avoid import cycles during early wiring
    from .logging import LoggingMiddleware
    from .rate_limit import RateLimitMiddleware
    from .cors import StrictCORSMiddleware

    logging_cfg = _extract_logging(config)
    rate_cfg = _extract_rate_limits(config)
    cors_cfg = _extract_cors(config)

    if logging_cfg["enabled"]:
        app.add_middleware(
            LoggingMiddleware,
            request_body_sample=logging_cfg["request_body_sample"],
        )

    if rate_cfg["enabled"]:
        app.add_middleware(
            RateLimitMiddleware,
            capacity=rate_cfg["capacity"],
            refill_rate_per_sec=rate_cfg["refill_rate_per_sec"],
            burst=rate_cfg["burst"],
            per_ip=rate_cfg["per_ip"],
            per_method=rate_cfg["per_method"],
        )

    app.add_middleware(
        StrictCORSMiddleware,
        allow_origins=cors_cfg["allow_origins"],
        allow_methods=cors_cfg["allow_methods"],
        allow_headers=cors_cfg["allow_headers"],
        allow_credentials=cors_cfg["allow_credentials"],
    )
