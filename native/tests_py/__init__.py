"""
Python-side tests for the `animica_native` bindings.

This package is collected by pytest (e.g. `pytest -q native/tests_py`) and expects
the Rust crate to be built and importable as a Python module. If imports fail, the
entire package is skipped with a helpful message.

Quick start
-----------
1) Build the native module:
   - Fast dev path:      `maturin develop`   (installs an editable debug build)
   - Wheel then install: `make wheel && pip install dist/*.whl`
   - Cargo-only (for Rust tests): `make build`
2) Run tests:
   - All python-native tests: `pytest -q native/tests_py`
   - Verbose + logs:          `pytest -qvv native/tests_py -s`

Environment toggles
-------------------
- ANIMICA_NATIVE_SKIP_HEAVY=1
    Skip heavy/long-running tests (large NMT/RS benches, huge payloads).
- ANIMICA_TEST_SEED=<int>
    Seed for deterministic RNG in tests that support it (default: 0 meaning "auto").
- ANIMICA_RS_BACKEND=rust|isal
    Hint for RS backend selection, if the native crate exposes such a knob.
- RAYON_NUM_THREADS=<n>
    Control thread pool size for parallelized paths (when feature "rayon" is enabled).
- CI=true
    Some tests may tighten timeouts when running in CI.

Notes
-----
- We intentionally keep imports centralized here so that a missing native module
  aborts early and with context, rather than failing later with ImportError in
  individual test modules.
- Test modules can import `native` (an alias to `animica_native`) and helpers from
  this package without repeating import/skip logic.

"""

from __future__ import annotations

import os
from typing import Optional

# ---------- Environment flags ----------

SKIP_HEAVY: bool = os.getenv("ANIMICA_NATIVE_SKIP_HEAVY", "0").lower() in {
    "1",
    "true",
    "yes",
}
TEST_SEED_RAW: str = os.getenv("ANIMICA_TEST_SEED", "").strip()
try:
    TEST_SEED: Optional[int] = int(TEST_SEED_RAW) if TEST_SEED_RAW else None
except ValueError:
    # Non-integer seeds are ignored; tests that need determinism will fall back.
    TEST_SEED = None


def is_ci() -> bool:
    """Return True when running under CI (GitHub Actions / generic CI env)."""
    return (
        os.getenv("CI", "").lower() == "true"
        or os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    )


# ---------- Native import & package-wide skip ----------

try:
    import pytest  # type: ignore
except Exception:
    # If pytest isn't available, do not hard-fail import; consumers might only want helpers.
    pytest = None  # type: ignore

try:
    import animica_native as _native  # Provided by pyo3 module; __init__ re-exports the Rust impl

    _HAVE_NATIVE = True
    _IMPORT_ERROR = None
except Exception as _e:  # noqa: N816 (intentional constant-like name)
    _HAVE_NATIVE = False
    _IMPORT_ERROR = _e

# If pytest is present and native module isn't importable, skip the whole package.
if pytest is not None and not _HAVE_NATIVE:
    pytest.skip(
        "animica_native not available: "
        f"{_IMPORT_ERROR!r}\n"
        "Build with `maturin develop` or `make wheel && pip install dist/*.whl` before running tests.",
        allow_module_level=True,
    )

# Public alias for test modules to import directly from this package.
native = _native if _HAVE_NATIVE else None  # type: ignore

__all__ = [
    "native",
    "SKIP_HEAVY",
    "TEST_SEED",
    "is_ci",
]
