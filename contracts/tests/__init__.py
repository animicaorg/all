# -*- coding: utf-8 -*-
"""
contracts.tests
================

Tiny utility package for contract-focused tests. This module keeps local runs
deterministic and provides a couple of safe environment defaults so example
tests and tooling behave consistently out of the box.

What it does:
- Sets reproducibility-friendly env defaults (override via real env when needed).
- Exposes a conventional project test seed for repeatable RNG in tests.
- Carries a simple version banner.

Note:
If you rely on TZ=UTC for time-sensitive tests and your runner supports it,
you may still need to call time.tzset() once in your test session (e.g., in a
pytest conftest.py) for the process to pick up the TZ variable on some OSes.
"""
from __future__ import annotations

import os

__version__ = "0.1.0"


def _set_if_absent(key: str, value: str) -> None:
    """Set environment variable only if it's not already present."""
    if key not in os.environ or os.environ.get(key) in ("", None):
        os.environ[key] = value


# --- Determinism & sensible local defaults ----------------------------------
# These are harmless if already provided by CI or user env; they simply make
# ad-hoc local runs behave predictably without extra setup.

# Python hash determinism (affects dict/set iteration order in some cases).
_set_if_absent("PYTHONHASHSEED", "0")

# Prefer UTC in tests (block timestamps, deadlines, timelocks in examples).
_set_if_absent("TZ", "UTC")

# Contracts toolchain defaults (overridable):
# - Local devnet chain id used across examples in this repository
_set_if_absent("ANIMICA_CHAIN_ID", "1337")
# - Default RPC target for quickstarts (matches devnet scripts)
_set_if_absent("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
# - Ask the Python VM to run in strict/deterministic mode where supported
_set_if_absent("ANIMICA_STRICT_VM", "1")

# Conventional seed tests may import to make pseudo-random choices reproducible.
PROJECT_TEST_SEED: int = 1337

__all__ = ["__version__", "PROJECT_TEST_SEED"]
