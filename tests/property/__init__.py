# -*- coding: utf-8 -*-
"""
tests.property package bootstrap.

Shared configuration for property-based tests (Hypothesis), kept intentionally
lightweight so importing this package has no external deps beyond Hypothesis.

What this does on import:
- Registers a few named Hypothesis profiles (dev/ci/fast/stress) with sane
  defaults for this repo.
- Selects the active profile using HYPOTHESIS_PROFILE, otherwise "ci" on CI
  (CI env var present/truthy) and "dev" locally.
- Re-exports common Hypothesis imports (given, strategies as st) for convenience.

Usage in tests:
    from tests.property import st, given

    @given(st.binary(min_size=1, max_size=1024))
    def test_something_roundtrips(b):
        ...

Environment knobs:
- HYPOTHESIS_PROFILE=dev|ci|fast|stress
- CI=true (auto-pick the 'ci' profile if HYPOTHESIS_PROFILE not set)

Note: If you need per-test overrides, just use @settings(...) on that test.
"""
from __future__ import annotations

import os
from typing import Final, Iterable, Tuple

from hypothesis import HealthCheck, Verbosity, given, settings
from hypothesis import strategies as st

# ---- profile registry --------------------------------------------------------


def _hc(*items: HealthCheck) -> Tuple[HealthCheck, ...]:
    # Small helper to placate type checkers
    return items


# Reasonable defaults for this repo:
# - Disable deadlines by default to reduce flakiness in slow CI machines.
# - Suppress 'too_slow' health check; we care more about correctness than perf here.
# - Raise verbosity a bit in CI to improve failure logs.
settings.register_profile(
    "dev",
    settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=_hc(
            HealthCheck.too_slow,
            HealthCheck.filter_too_much,
        ),
        verbosity=Verbosity.normal,
        derandomize=False,  # allow true randomness locally
    ),
)

settings.register_profile(
    "ci",
    settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=_hc(
            HealthCheck.too_slow,
            HealthCheck.filter_too_much,
        ),
        verbosity=Verbosity.verbose,
        derandomize=True,  # reduce flakiness; deterministic example generation
    ),
)

settings.register_profile(
    "fast",
    settings(
        max_examples=25,
        deadline=None,
        suppress_health_check=_hc(HealthCheck.too_slow),
        verbosity=Verbosity.normal,
        derandomize=False,
    ),
)

settings.register_profile(
    "stress",
    settings(
        max_examples=1000,
        deadline=None,
        suppress_health_check=_hc(
            HealthCheck.too_slow,
            HealthCheck.filter_too_much,
            HealthCheck.data_too_large,
        ),
        verbosity=Verbosity.normal,
        derandomize=True,
    ),
)


def _env_truthy(name: str) -> bool:
    v = os.getenv(name)
    return (v or "").lower() not in ("", "0", "false", "no", "off")


# Choose active profile (env overrides CI detection)
_active: Final[str] = os.getenv("HYPOTHESIS_PROFILE") or (
    "ci" if _env_truthy("CI") else "dev"
)
settings.load_profile(_active)

# ---- small convenience exports ----------------------------------------------


def is_ci() -> bool:
    """Return True if we appear to be running under CI."""
    return _env_truthy("CI")


def active_profile() -> str:
    """Return the name of the active Hypothesis profile."""
    return _active


def bytes_nonempty(max_size: int = 4096):
    """Common binary strategy used across tests: non-empty bytes up to max_size."""
    return st.binary(min_size=1, max_size=max_size)


__all__ = [
    # hypothesis conveniences
    "st",
    "given",
    # helpers
    "is_ci",
    "active_profile",
    "bytes_nonempty",
]
