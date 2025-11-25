#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run the "fast" test suite: unit + light module tests, no Docker/devnet required.

What this runs by default:
- tests/unit/
- Module unit tests if present: pq/, core/, rpc/, consensus/, proofs/, mining/, p2p/,
  mempool/, da/, execution/, vm_py/, capabilities/, aicf/, randomness/
- Optionally property tests (tests/property) with --include-property

What it skips:
- tests/integration, tests/e2e, tests/load, tests/bench, tests/fuzz, tests/devnet
- Anything marked/keyworded as e2e, integration, docker, devnet, load, bench, fuzz

Reports (written under tests/reports/):
- JUnit XML: junit-fast.xml
- Coverage: HTML (coverage-fast-html) + XML (coverage-fast.xml)

Usage:
  python tests/ci/run_fast_suite.py
  python tests/ci/run_fast_suite.py --include-property
  python tests/ci/run_fast_suite.py --extra "-k 'not slow'"
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List

# Ensure repo root on PYTHONPATH
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# Best-effort CI snapshot for logs
try:
    from tests.ci import env_snapshot  # type: ignore
except Exception:  # pragma: no cover
    def env_snapshot():
        return {"python": sys.version.split()[0], "implementation": sys.implementation.name, "platform": sys.platform, "ci": "unknown"}


def discover_test_roots(include_property: bool) -> List[str]:
    """Return list of test directories to run."""
    roots: List[Path] = []

    # Always include core unit tests root if present
    candidates = [
        REPO / "tests" / "unit",
        # Module-local unit tests
        REPO / "pq" / "tests",
        REPO / "core" / "tests",
        REPO / "rpc" / "tests",
        REPO / "consensus" / "tests",
        REPO / "proofs" / "tests",
        REPO / "mining" / "tests",
        REPO / "p2p" / "tests",
        REPO / "mempool" / "tests",
        REPO / "da" / "tests",
        REPO / "execution" / "tests",
        REPO / "vm_py" / "tests",
        REPO / "capabilities" / "tests",
        REPO / "aicf" / "tests",
        REPO / "randomness" / "tests",
        # Optionally property tests
        REPO / "tests" / "property" if include_property else Path("__skip__"),
    ]
    for p in candidates:
        if p.name == "__skip__":
            continue
        if p.is_dir():
            roots.append(p)

    return [str(p.relative_to(REPO)) for p in roots]


def discover_cov_packages() -> List[str]:
    """Return list of top-level packages to measure coverage for (if present)."""
    pkgs = [
        "pq",
        "core",
        "rpc",
        "consensus",
        "proofs",
        "mining",
        "p2p",
        "mempool",
        "da",
        "execution",
        "vm_py",
        "capabilities",
        "aicf",
        "randomness",
        "studio_services",  # package dir under studio-services
    ]
    found: List[str] = []
    for name in pkgs:
        # Resolve dash-name to folder for studio-services
        folder = name if name != "studio_services" else "studio-services/studio_services"
        if (REPO / folder).exists():
            found.append(name)
    return found


def have_plugin(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fast test suite (unit + light).")
    parser.add_argument("--include-property", action="store_true", help="Also run property tests (tests/property).")
    parser.add_argument("--report-dir", default="tests/reports", help="Directory to write reports into.")
    parser.add_argument("--junit-name", default="junit-fast.xml", help="JUnit XML file name in report dir.")
    parser.add_argument("--cov-html-dir", default="coverage-fast-html", help="Coverage HTML subdir in report dir.")
    parser.add_argument("--cov-xml-name", default="coverage-fast.xml", help="Coverage XML file name in report dir.")
    parser.add_argument("--extra", default="", help="Extra pytest args (quoted).")
    args = parser.parse_args()

    report_dir = REPO / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    # Build pytest command
    pytest_cmd: List[str] = [sys.executable, "-m", "pytest"]

    # Quiet and friendly failure summary
    pytest_cmd += ["-q", "-ra", "--maxfail=1", "--durations=10", "--strict-config", "--strict-markers"]

    # Exclude slow/e2e-ish by keyword (defensive even if we don't pass those dirs)
    pytest_cmd += [
        "-k",
        "not e2e and not integration and not docker and not devnet and not load and not bench and not fuzz",
    ]

    # JUnit
    junit_path = report_dir / args.junit_name
    pytest_cmd += ["--junitxml", str(junit_path)]

    # Coverage if pytest-cov is available
    cov_sources = discover_cov_packages()
    if have_plugin("pytest_cov") and cov_sources:
        for src in cov_sources:
            pytest_cmd += ["--cov", src]
        cov_xml_path = report_dir / args.cov_xml_name
        cov_html_dir = report_dir / args.cov_html_dir
        pytest_cmd += [
            "--cov-report=term-missing:skip-covered",
            f"--cov-report=xml:{cov_xml_path}",
            f"--cov-report=html:{cov_html_dir}",
        ]

    # Parallellism if xdist present
    if have_plugin("xdist"):
        pytest_cmd += ["-n", "auto"]

    # Extra user-provided args
    if args.extra:
        pytest_cmd += shlex.split(args.extra)

    # Test roots
    roots = discover_test_roots(include_property=args.include_property)
    if not roots:
        print("No test roots found for fast suite. Exiting 0.")
        return 0
    pytest_cmd += roots

    # Print a tiny preamble for logs
    snap = env_snapshot()
    print("== Animica Fast Test Suite ==")
    print(f"repo: {REPO}")
    print(f"python: {snap.get('python')} ({snap.get('implementation')}) on {snap.get('platform')}, CI={snap.get('ci')}")
    print("test roots:", ", ".join(roots))
    print("coverage:", ", ".join(discover_cov_packages()) or "(none)")
    print("reports:", junit_path, " | ", (report_dir / args.cov_html_dir), " | ", (report_dir / args.cov_xml_name))
    print("pytest cmd:", " ".join(shlex.quote(x) for x in pytest_cmd))
    print("================================\n")

    # Run
    env = os.environ.copy()
    # Friendly defaults for reproducibility
    env.setdefault("PYTHONHASHSEED", "0")
    env.setdefault("HYPOTHESIS_PROFILE", "default")  # if property tests run
    env.setdefault("NUMBA_DISABLE_JIT", "1")  # in case optional numba exists, keep deterministic

    proc = subprocess.run(pytest_cmd, cwd=str(REPO), env=env)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
