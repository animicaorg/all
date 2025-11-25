#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run the FULL Animica test suite:

- Spins up the devnet (Docker Compose) unless --no-docker is set
- Waits for RPC/WS/services readiness
- Runs integration tests (pytest)
- Runs E2E flows (Playwright-backed helper scripts)
- Runs short "smoke" fuzzers with Atheris (if installed)

Skipped by default: load & bench suites (heavy/long-running).

Reports -> tests/reports/:
- JUnit: junit-full.xml
- Coverage: coverage-full.xml, coverage-full-html/

Usage:
  python tests/ci/run_full_suite.py
  python tests/ci/run_full_suite.py --no-docker
  python tests/ci/run_full_suite.py --skip-e2e --skip-fuzz
  python tests/ci/run_full_suite.py --fuzz-seconds 15
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple


REPO = Path(__file__).resolve().parents[2]
REPORT_DIR_DEFAULT = REPO / "tests" / "reports"
DEVNET_DIR = REPO / "tests" / "devnet"
COMPOSE_FILE_DEFAULT = DEVNET_DIR / "docker-compose.yml"
ENV_FILE_DEFAULT = DEVNET_DIR / "env.devnet.example"
WAIT_SCRIPT_DEFAULT = DEVNET_DIR / "wait_for_services.sh"


# ---------- utilities ----------

def log(msg: str) -> None:
    print(f"[full-suite] {msg}", flush=True)


def run(cmd: List[str], cwd: Path | None = None, env: Dict[str, str] | None = None) -> int:
    log("RUN: " + " ".join(shlex.quote(x) for x in cmd))
    return subprocess.run(cmd, cwd=str(cwd or REPO), env=env).returncode


def find_compose_cmd() -> List[str]:
    # Prefer v2 `docker compose`, fallback to legacy `docker-compose`
    code = subprocess.run(["docker", "compose", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if code.returncode == 0:
        return ["docker", "compose"]
    return ["docker-compose"]


def have_module(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def ensure_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)
    except Exception:
        pass


# ---------- phases ----------

def phase_devnet_up(compose_file: Path, env_file: Path, compose_opts: List[str]) -> Tuple[bool, List[str]]:
    compose = find_compose_cmd()
    base = compose + ["-f", str(compose_file)]
    if env_file.exists():
        base += ["--env-file", str(env_file)]
    cmd = base + ["up", "-d"] + compose_opts
    rc = run(cmd, cwd=DEVNET_DIR)
    return rc == 0, compose


def phase_devnet_wait(wait_script: Path, timeout: int) -> bool:
    ensure_executable(wait_script)
    env = os.environ.copy()
    env.setdefault("WAIT_TIMEOUT", str(timeout))
    rc = run([str(wait_script)], cwd=DEVNET_DIR, env=env)
    return rc == 0


def phase_pytest_integration(report_dir: Path) -> int:
    report_dir.mkdir(parents=True, exist_ok=True)

    junit_path = report_dir / "junit-full.xml"
    cov_xml = report_dir / "coverage-full.xml"
    cov_html_dir = report_dir / "coverage-full-html"

    # Coverage packages (best effort)
    cov_targets = [
        "pq","core","rpc","consensus","proofs","mining","p2p","mempool",
        "da","execution","vm_py","capabilities","aicf","randomness","studio_services",
    ]

    pytest_cmd: List[str] = [sys.executable, "-m", "pytest", "-q", "-ra", "--maxfail=1", "--durations=20"]
    pytest_cmd += ["--junitxml", str(junit_path)]

    # Strict markers/config
    pytest_cmd += ["--strict-config", "--strict-markers"]

    # Prefer parallel if xdist installed
    if have_module("xdist"):
        pytest_cmd += ["-n", "auto"]

    # Coverage if plugin present
    if have_module("pytest_cov"):
        for t in cov_targets:
            # studio_services folder lives under studio-services/
            if t == "studio_services":
                if not (REPO / "studio-services" / "studio_services").exists():
                    continue
            else:
                if not (REPO / t).exists():
                    continue
            pytest_cmd += ["--cov", t]
        pytest_cmd += [
            "--cov-report=term-missing:skip-covered",
            f"--cov-report=xml:{cov_xml}",
            f"--cov-report=html:{cov_html_dir}",
        ]

    # Deselect heavy suites; we'll run E2E separately
    pytest_cmd += ["-k", "not e2e and not docker and not bench and not load and not fuzz"]

    # Target directories
    pytest_cmd += [
        "tests/integration",
        "tests/property",  # include property tests in full run
    ]

    env = os.environ.copy()
    env.setdefault("PYTHONHASHSEED", "0")
    env.setdefault("NUMBA_DISABLE_JIT", "1")
    env.setdefault("HYPOTHESIS_PROFILE", "ci")

    return run(pytest_cmd, cwd=REPO, env=env)


def phase_e2e() -> int:
    # Run the three E2E drivers (they manage Playwright internally)
    e2e_scripts = [
        "tests/e2e/run_wallet_extension_e2e.py",
        "tests/e2e/run_studio_web_deploy_template.py",
        "tests/e2e/run_explorer_live_dashboard.py",
    ]
    env = os.environ.copy()
    # Helpful defaults: keep deterministic fonts/graphics; avoid sandbox issues in CI
    env.setdefault("CI", os.getenv("CI", "true"))
    env.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")  # if baked into CI image
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    env.setdefault("PYTHONHASHSEED", "0")

    cumulative_rc = 0
    for script in e2e_scripts:
        rc = run([sys.executable, script], cwd=REPO, env=env)
        cumulative_rc = cumulative_rc or rc
    return cumulative_rc


def phase_fuzz_smoke(seconds: int) -> int:
    if not have_module("atheris"):
        log("Atheris not installed; skipping fuzz smoke.")
        return 0

    # Map fuzz targets → (corpus dir, optional dict path)
    targets: Dict[str, Tuple[str, str | None]] = {
        "tests/fuzz/fuzz_tx_decode.py": ("tests/fuzz/corpus_txs", "tests/fuzz/dictionaries/cbor.dict"),
        "tests/fuzz/fuzz_block_decode.py": ("tests/fuzz/corpus_blocks", "tests/fuzz/dictionaries/cbor.dict"),
        "tests/fuzz/fuzz_proof_envelopes.py": ("tests/fuzz/corpus_proofs", "tests/fuzz/dictionaries/cbor.dict"),
        "tests/fuzz/fuzz_vm_ir.py": ("tests/fuzz/corpus_vm_ir", "tests/fuzz/dictionaries/cbor.dict"),
        "tests/fuzz/fuzz_p2p_messages.py": ("tests/fuzz/corpus_blocks", "tests/fuzz/dictionaries/cbor.dict"),
        "tests/fuzz/fuzz_nmt_proofs.py": ("tests/fuzz/corpus_blocks", "tests/fuzz/dictionaries/cbor.dict"),
        "tests/fuzz/fuzz_randomness_inputs.py": ("tests/fuzz/corpus_blocks", "tests/fuzz/dictionaries/json.dict"),
    }

    env = os.environ.copy()
    env.setdefault("PYTHONHASHSEED", "0")

    cumulative_rc = 0
    runner = REPO / "tests" / "fuzz" / "atheris_runner.py"
    for target, (corpus, dict_path) in targets.items():
        cmd = [sys.executable, str(runner), "--target", target, "--seconds", str(seconds), "--corpus", corpus]
        if dict_path:
            cmd += ["--dict", dict_path]
        rc = run(cmd, cwd=REPO, env=env)
        cumulative_rc = cumulative_rc or rc
    return cumulative_rc


def phase_devnet_down(compose_bin: List[str], compose_file: Path, env_file: Path) -> int:
    base = compose_bin + ["-f", str(compose_file)]
    if env_file.exists():
        base += ["--env-file", str(env_file)]
    return run(base + ["down", "-v"], cwd=DEVNET_DIR)


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="Run full devnet + integration + e2e + fuzz-smoke suite.")
    parser.add_argument("--no-docker", action="store_true", help="Assume devnet is already running; skip compose up/down.")
    parser.add_argument("--compose-file", default=str(COMPOSE_FILE_DEFAULT), help="Path to docker-compose.yml")
    parser.add_argument("--env-file", default=str(ENV_FILE_DEFAULT), help="Path to env file for compose")
    parser.add_argument("--compose-opt", action="append", default=[], help="Extra options for `docker compose up`")
    parser.add_argument("--wait-timeout", type=int, default=180, help="Seconds to wait for services to be ready")
    parser.add_argument("--skip-e2e", action="store_true", help="Skip E2E runners")
    parser.add_argument("--skip-fuzz", action="store_true", help="Skip fuzz smoke")
    parser.add_argument("--fuzz-seconds", type=int, default=10, help="Seconds per fuzz target in smoke pass")
    parser.add_argument("--report-dir", default=str(REPORT_DIR_DEFAULT), help="Directory for junit/coverage reports")

    args = parser.parse_args()

    # Paths
    compose_file = Path(args.compose_file)
    env_file = Path(args.env_file)
    wait_script = Path(WAIT_SCRIPT_DEFAULT)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    started_compose = False
    compose_bin: List[str] = []

    # Deterministic-friendly env tweaks
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

    try:
        if not args.no_docker:
            log("Bringing up devnet via Docker Compose…")
            ok, compose_bin = phase_devnet_up(compose_file, env_file, args.compose_opt)
            if not ok:
                log("Compose up failed.")
                return 2
            started_compose = True

            log("Waiting for services to become ready…")
            if not phase_devnet_wait(wait_script, args.wait_timeout):
                log("Wait for services failed or timed out.")
                return 3
        else:
            log("--no-docker set: skipping devnet up/wait.")

        # Integration + property tests
        log("Running integration + property tests (pytest)…")
        rc_integ = phase_pytest_integration(report_dir)
        if rc_integ != 0:
            log(f"Integration/property tests failed with rc={rc_integ}")

        # E2E
        rc_e2e = 0
        if not args.skip_e2e:
            log("Running E2E scripts…")
            rc_e2e = phase_e2e()
            if rc_e2e != 0:
                log(f"E2E scripts failed with rc={rc_e2e}")
        else:
            log("Skipping E2E scripts.")

        # Fuzz smoke
        rc_fuzz = 0
        if not args.skip_fuzz:
            log(f"Running fuzz smoke (≈{args.fuzz_seconds}s per target)…")
            rc_fuzz = phase_fuzz_smoke(args.fuzz_seconds)
            if rc_fuzz != 0:
                log(f"Fuzz smoke had failures with rc={rc_fuzz}")
        else:
            log("Skipping fuzz smoke.")

        final_rc = 0
        for name, rc in (("integration", rc_integ), ("e2e", rc_e2e), ("fuzz", rc_fuzz)):
            final_rc = final_rc or rc
        if final_rc == 0:
            log("✅ Full suite passed.")
        else:
            log("❌ Full suite failed.")

        return final_rc
    finally:
        if started_compose:
            log("Tearing down devnet (docker compose down -v)…")
            try:
                phase_devnet_down(compose_bin, compose_file, env_file)
            except Exception as exc:
                log(f"Compose down raised: {exc}")


if __name__ == "__main__":
    sys.exit(main())
