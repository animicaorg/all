"""
Nox multi-session runner for Animica Python components.

Sessions:
  - lint   : ruff + black + mypy over Python sources
  - unit   : fast unit tests (no slow/integ/e2e/fuzz/bench markers)
  - integ  : integration tests (requires a devnet or configured RPC)
  - fuzz   : atheris-based fuzz tests (CPython 3.10/3.11 only)
  - bench  : performance/benchmark tests (marked 'bench')
  - cov    : combine parallel coverage and produce reports

Pass extra args to pytest like:
  nox -s unit -- -k "wallet and not slow" -vv
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import nox

# Reuse envs to speed up local iteration
nox.options.reuse_venv = True
nox.options.stop_on_first_error = False

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_PATHS = [
    "sdk/python/omni_sdk",
    "studio-services/studio_services",
    "studio-wasm/py/vm_pkg",
    "studio-wasm/py/bridge",
    "tests",
]

# Default python matrix for test sessions
TEST_PYTHONS = ["3.10", "3.11", "3.12"]

COVERAGE_RC = str(REPO_ROOT / "tests" / "coverage.rc")
REQS_FILE = str(REPO_ROOT / "tests" / "requirements.txt")


def _common_env(session: nox.Session) -> None:
    """Environment shaping for tests so imports 'just work' without wheels."""
    # Ensure in-repo Python packages are importable when not installed.
    # studio-wasm/py isn't a proper package to pip install, so keep it on PYTHONPATH.
    extra_paths = [
        str(REPO_ROOT / "studio-wasm" / "py"),
        str(REPO_ROOT / "sdk" / "python"),
        str(REPO_ROOT / "studio-services"),
    ]
    existing = session.env.get("PYTHONPATH", "")
    joined = os.pathsep.join(
        p for p in ([existing] if existing else []) + extra_paths if p
    )
    session.env["PYTHONPATH"] = joined

    # Propagate useful CI toggles if present
    session.env.setdefault("PYTHONUNBUFFERED", "1")


def _install_test_stack(session: nox.Session) -> None:
    session.run("python", "-m", "pip", "install", "--upgrade", "pip", silent=True)
    # Core test deps
    session.install("-r", REQS_FILE)
    # Editable local modules used across tests
    # omni_sdk (Python)
    session.install("-e", str(REPO_ROOT / "sdk" / "python"))
    # studio-services (FastAPI)
    session.install("-e", str(REPO_ROOT / "studio-services"))


@nox.session(name="lint", python="3.11")
def lint(session: nox.Session) -> None:
    """Static analysis: ruff, black (check), mypy."""
    _common_env(session)
    session.run("python", "-m", "pip", "install", "--upgrade", "pip", silent=True)
    session.install(
        "ruff>=0.6.0",
        "black>=24.3.0",
        "mypy>=1.10.0",
        "types-requests",
        "types-setuptools",
    )
    # mypy needs runtime deps to import modules; install light editable installs
    session.install("-e", str(REPO_ROOT / "sdk" / "python"))
    session.install("-e", str(REPO_ROOT / "studio-services"))

    # Ruff
    session.run("ruff", "check", *PY_PATHS)
    # Black
    session.run("black", "--check", *PY_PATHS)
    # Mypy (be strict on our packages; tests can be looser)
    mypy_targets = [
        "sdk/python/omni_sdk",
        "studio-services/studio_services",
        "studio-wasm/py",
    ]
    session.run(
        "mypy",
        "--pretty",
        "--show-error-codes",
        "--ignore-missing-imports",
        *mypy_targets,
    )


@nox.session(name="unit", python=TEST_PYTHONS)
def unit(session: nox.Session) -> None:
    """Fast unit tests only (no slow/integ/e2e/fuzz/bench)."""
    _common_env(session)
    _install_test_stack(session)
    pytest_args = [
        "-c",
        COVERAGE_RC,
        "-m",
        "not slow and not integ and not e2e and not fuzz and not bench",
        "-q",
        *session.posargs,
    ]
    # Coverage is configured via coverage.rc [run] section; just invoke pytest
    session.run("pytest", *pytest_args)


@nox.session(name="integ", python=TEST_PYTHONS)
def integ(session: nox.Session) -> None:
    """Integration tests (may hit a live devnet/RPC)."""
    _common_env(session)
    _install_test_stack(session)

    # Helpful defaults if not set (these match tests/.env.example but are safe to override)
    session.env.setdefault("RPC_URL", "http://127.0.0.1:8545")
    session.env.setdefault("CHAIN_ID", "1337")

    pytest_args = [
        "-c",
        COVERAGE_RC,
        "-m",
        "integ",
        "-vv",
        *session.posargs,
    ]
    session.run("pytest", *pytest_args)


@nox.session(name="fuzz", python=["3.10", "3.11"])
def fuzz(session: nox.Session) -> None:
    """Atheris-driven fuzz tests (markers: fuzz)."""
    _common_env(session)
    _install_test_stack(session)

    # Atheris hints (no forkserver in CI containers)
    session.env.setdefault("AFL_SKIP_CPUFREQ", "1")
    session.env.setdefault("ATHERIS_NO_CLD", "1")

    # By default, run pytest-based fuzz harnesses marked with 'fuzz'.
    # You can pass extra args to narrow targets, e.g.: nox -s fuzz -- -k "cbor"
    pytest_args = [
        "-c",
        COVERAGE_RC,
        "-m",
        "fuzz",
        "-vv",
        *session.posargs,
    ]
    session.run("pytest", *pytest_args)


@nox.session(name="bench", python=TEST_PYTHONS)
def bench(session: nox.Session) -> None:
    """Performance/benchmark tests (markers: bench)."""
    _common_env(session)
    _install_test_stack(session)
    pytest_args = [
        "-c",
        COVERAGE_RC,
        "-m",
        "bench",
        "-vv",
        *session.posargs,
    ]
    session.run("pytest", *pytest_args)


@nox.session(name="cov", python="3.11")
def cov(session: nox.Session) -> None:
    """
    Combine & report coverage from parallel runs.
    Usage:
      nox -s unit-3.11 unit-3.12 integ-3.11 ...
      nox -s cov
    """
    session.run("python", "-m", "pip", "install", "--upgrade", "pip", silent=True)
    session.install("coverage>=7.4.0")
    # Ensure we run from repo root so [paths] mapping applies
    with session.chdir(str(REPO_ROOT)):
        session.run("coverage", "combine")
        session.run("coverage", "report", "-m", "-c", COVERAGE_RC)
        session.run("coverage", "html", "-c", COVERAGE_RC)
        session.log(
            f"HTML report: {REPO_ROOT / 'tests' / '.coverage_html' / 'index.html'}"
        )


# Convenience: `nox -s all` to run lint + unit on default python + integ quick pass
@nox.session(name="all", python="3.11")
def all_(session: nox.Session) -> None:
    """Run a sensible default stack locally."""
    session.notify("lint")
    session.notify("unit-3.11")
    # Integration on one interpreter to keep it quick
    session.notify("integ-3.11")
