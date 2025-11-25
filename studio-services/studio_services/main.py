"""
Uvicorn launcher for Animica Studio Services.

Usage:
  python -m studio_services.main [--host 0.0.0.0] [--port 8080]
                                 [--workers 1] [--reload]
                                 [--log-level info]

Environment overrides (if flags not provided):
  HOST / BIND, PORT, WORKERS, RELOAD, LOG_LEVEL
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

import uvicorn

from .config import load_config


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "y", "on")


def main(argv: Optional[list[str]] = None) -> None:
    cfg = load_config()  # used for defaults when available

    default_host = os.getenv("HOST") or os.getenv("BIND") or getattr(cfg, "host", "0.0.0.0")
    default_port = int(os.getenv("PORT") or getattr(cfg, "port", 8080))
    default_workers = int(os.getenv("WORKERS") or 1)
    default_reload = _env_bool("RELOAD", False)
    default_log_level = os.getenv("LOG_LEVEL", "info")

    parser = argparse.ArgumentParser(description="Run Animica Studio Services (uvicorn)")
    parser.add_argument("--host", default=default_host, help="Bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=default_port, help="Port (default: %(default)s)")
    parser.add_argument("--workers", type=int, default=default_workers, help="Number of workers (default: %(default)s)")
    parser.add_argument("--reload", action="store_true", default=default_reload, help="Enable autoreload (dev only)")
    parser.add_argument("--log-level", default=default_log_level, help="Log level for uvicorn (default: %(default)s)")
    parser.add_argument("--proxy-headers", action="store_true", default=True, help="Use X-Forwarded-* headers (default: on)")
    parser.add_argument("--forwarded-allow-ips", default="*", help="Comma list of trusted proxies (default: *)")

    args = parser.parse_args(argv)

    # Safety: uvicorn doesn't support workers>1 with a Python app object unless using factory import string.
    # Also, reload and workers>1 are mutually exclusive; prefer reload for dev if both set.
    if args.reload and args.workers != 1:
        print("[studio-services] --reload implies --workers=1; overriding.")
        args.workers = 1

    uvicorn_kwargs = dict(
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        proxy_headers=args.proxy_headers,
        forwarded_allow_ips=args.forwarded_allow_ips,
        reload=args.reload,
        workers=args.workers,
        # http/loop defaults chosen by uvicorn; can be tuned via env if needed
    )

    # Use factory import string so multi-worker works and config is constructed per-worker.
    # Uvicorn will call studio_services.app:create_app() in each worker.
    uvicorn.run("studio_services.app:create_app", factory=True, **uvicorn_kwargs)


if __name__ == "__main__":
    main()
