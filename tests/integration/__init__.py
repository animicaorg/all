# -*- coding: utf-8 -*-
"""
Integration test package.

These tests are intended to exercise real components end-to-end (DBs, RPC,
p2p, DA service, etc.). By default they are **skipped** to keep CI fast and to
avoid flakiness on machines without the required services.

Enable them explicitly by setting:
    RUN_INTEGRATION_TESTS=1

Common environment knobs (with safe defaults if a test opts-in):
    ANIMICA_DB_URL       — e.g. sqlite:///animica_it.db
    ANIMICA_RPC_URL      — e.g. http://127.0.0.1:8545
    ANIMICA_WS_URL       — e.g. ws://127.0.0.1:8546/ws
    ANIMICA_DA_URL       — e.g. http://127.0.0.1:8787
    ANIMICA_CHAIN_ID     — e.g. 1 (int), used by tx/build/verify flows

Typical usage inside a test:
    from tests.integration import env, require_env
    rpc = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    require_env("ANIMICA_RPC_URL")  # skips the test with a helpful message if missing

You can also mark a whole module to be skipped unless RUN_INTEGRATION_TESTS=1 by
importing this package at module import time (see below).
"""
from __future__ import annotations

import os
from typing import Optional

import pytest


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    value = value.strip().lower()
    return value in ("1", "true", "yes", "y", "on")


# Package-level gate: skip the *package* unless explicitly enabled.
if not _as_bool(os.getenv("RUN_INTEGRATION_TESTS"), default=False):
    pytest.skip(
        "integration tests disabled — set RUN_INTEGRATION_TESTS=1 to enable",
        allow_module_level=True,
    )


# Handy defaults some tests might choose to use (tests may override per-case).
DEFAULTS = {
    "ANIMICA_DB_URL": "sqlite:///animica_it.db",
    "ANIMICA_RPC_URL": "http://127.0.0.1:8545",
    "ANIMICA_WS_URL": "ws://127.0.0.1:8546/ws",
    "ANIMICA_DA_URL": "http://127.0.0.1:8787",
    "ANIMICA_CHAIN_ID": "1",
}


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Read an environment variable with an optional default. If default is None,
    we fall back to DEFAULTS (if present) for convenience.
    """
    if default is None:
        default = DEFAULTS.get(name)
    return os.getenv(name, default)


def require_env(name: str) -> str:
    """
    Assert that an environment variable is present; if not, skip the test with
    an actionable message.
    """
    val = os.getenv(name)
    if val is None:
        pytest.skip(
            f"Missing env var {name!r}; set it or provide a fixture to run this integration test."
        )
    return val


__all__ = ["env", "require_env", "DEFAULTS"]
