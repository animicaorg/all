# -*- coding: utf-8 -*-
"""
tests.devnet
============

Package marker for devnet-related test helpers.

This namespace is imported by several integration/E2E tests to spin up or
attach to a local Animica *devnet* (single-node by default), manage temporary
DBs, and provide convenience RPC/WS clients and funded accounts.

The concrete helpers (e.g., `ensure_devnet_running`, `funded_accounts`) may be
defined in companion modules next to specific tests, so we keep imports
optional here to avoid hard dependencies and keep `pytest -k ...` snappy.
"""
from __future__ import annotations

# Optional re-exports (these may be provided by tests/devnet/helpers.py or similar).
try:  # pragma: no cover
    from .helpers import ensure_devnet_running  # type: ignore
    from .helpers import (DevnetConfig, funded_accounts, rpc_client,
                          wait_for_heads, ws_client)
except Exception:  # pragma: no cover
    # Helpers are optional; tests that need them will import concrete modules directly.
    DevnetConfig = None  # type: ignore
    ensure_devnet_running = None  # type: ignore
    rpc_client = None  # type: ignore
    ws_client = None  # type: ignore
    funded_accounts = None  # type: ignore
    wait_for_heads = None  # type: ignore

__all__ = [
    "DevnetConfig",
    "ensure_devnet_running",
    "rpc_client",
    "ws_client",
    "funded_accounts",
    "wait_for_heads",
]
