"""
tests.harness
=============

Lightweight helpers shared by test suites across the repo.

Responsibilities
- Load test-time environment from `tests/.env` (or repo `.env`) if present.
- Provide typed accessors for common settings (RPC_URL, CHAIN_ID, timeouts).
- Offer tiny utilities used by fixtures without introducing heavy deps.

This module is intentionally dependency-light (stdlib only, with optional
`python-dotenv` if available). Import it freely from tests and fixtures:

    from tests.harness import (
        REPO_ROOT, TESTS_ROOT,
        DEFAULT_RPC_URL, DEFAULT_CHAIN_ID,
        env_str, env_int, is_truthy, get_logger,
    )
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "REPO_ROOT",
    "TESTS_ROOT",
    "load_env",
    "env_str",
    "env_int",
    "env_float",
    "is_truthy",
    "DEFAULT_RPC_URL",
    "DEFAULT_CHAIN_ID",
    "DEFAULT_HTTP_TIMEOUT",
    "DEFAULT_WS_TIMEOUT",
    "get_logger",
]

# Paths
TESTS_ROOT: Path = Path(__file__).resolve().parents[1]
REPO_ROOT: Path = TESTS_ROOT.parent


def _try_dotenv_load(dotenv_path: Path) -> None:
    """
    Best-effort loader for KEY=VALUE files without requiring python-dotenv.
    Minimal parsing: ignores blank lines and lines starting with '#'.
    """
    if not dotenv_path.is_file():
        return
    try:
        # Prefer python-dotenv if available (respects quoting, export, etc.)
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(dotenv_path, override=False)
        return
    except Exception:
        # Fall back to trivial parser
        pass

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def load_env() -> None:
    """
    Load environment for tests once. Idempotent on repeated calls.
    Search order:
      1) tests/.env
      2) .env at repo root
    """
    # Guard to avoid re-reading on multiple imports
    if os.environ.get("_HARNESS_ENV_LOADED") == "1":
        return

    test_env = TESTS_ROOT / ".env"
    root_env = REPO_ROOT / ".env"
    _try_dotenv_load(test_env)
    _try_dotenv_load(root_env)

    os.environ["_HARNESS_ENV_LOADED"] = "1"


# Load at import time so consumers see populated defaults
load_env()


def env_str(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get an environment variable as string (or default)."""
    return os.environ.get(key, default)


def env_int(key: str, default: Optional[int] = None) -> Optional[int]:
    """Get an environment variable as int (or default)."""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val, 0) if isinstance(val, str) else int(val)  # supports "0x..." too
    except Exception:
        return default


def env_float(key: str, default: Optional[float] = None) -> Optional[float]:
    """Get an environment variable as float (or default)."""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except Exception:
        return default


def is_truthy(val: Optional[str]) -> bool:
    """Interpret a string-ish environment value as boolean."""
    if val is None:
        return False
    return val.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


# Common test configuration (with sensible defaults for a local devnet)
DEFAULT_RPC_URL: str = env_str("RPC_URL", "http://127.0.0.1:8545") or "http://127.0.0.1:8545"
DEFAULT_CHAIN_ID: int = env_int("CHAIN_ID", 1337) or 1337

# Timeouts (seconds) used by HTTP/WS clients in tests
DEFAULT_HTTP_TIMEOUT: float = env_float("HTTP_TIMEOUT", 30.0) or 30.0
DEFAULT_WS_TIMEOUT: float = env_float("WS_TIMEOUT", 30.0) or 30.0


def get_logger(name: str = "tests.harness", level: int = logging.INFO) -> logging.Logger:
    """
    Return a module-level logger with a concise formatter.
    Honors LOG_LEVEL if set (DEBUG/INFO/WARN/ERROR).
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
        handler.setFormatter(logging.Formatter(fmt))
        logger.addHandler(handler)
    env_level = os.environ.get("LOG_LEVEL")
    if env_level:
        try:
            logger.setLevel(getattr(logging, env_level.upper()))
        except Exception:
            logger.setLevel(level)
    else:
        logger.setLevel(level)
    return logger
