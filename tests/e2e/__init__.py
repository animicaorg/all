# -*- coding: utf-8 -*-
"""
E2E test helpers.

These utilities are shared by end-to-end tests that may involve real
processes (devnet node, studio-services, browser automation), network
calls and longer timeouts.

Environment flags (conventions)
-------------------------------
• RUN_E2E_TESTS=1             — gate to enable E2E tests (default: skip)
• ANIMICA_RPC_URL             — RPC base URL (e.g., http://127.0.0.1:8545)
• STUDIO_SERVICES_URL         — studio-services base URL (optional)
• E2E_TIMEOUT                 — default per-step timeout in seconds (default: 120)
• E2E_BROWSER                 — hint for browser runner (chromium|firefox|webkit), if used by tests

Notes
-----
We intentionally keep this module stdlib-only so it works in hermetic CI.
For JSON-RPC convenience helpers, see tests.integration.__init__.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import (Any, Dict, Iterable, Iterator, List, Mapping, Optional,
                    Sequence, Tuple, Union)

import pytest

# Reuse basic env() helper from integration
try:
    from tests.integration import env as _integration_env  # type: ignore
except Exception:  # pragma: no cover - fallback if integration not present
    _integration_env = lambda k, d=None: os.getenv(k, d)  # noqa: E731


# ------------------------------- configuration -------------------------------


def env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch an environment variable (delegating to integration helper when available)."""
    return _integration_env(key, default)


def e2e_enabled() -> bool:
    """Return True if E2E tests are enabled via RUN_E2E_TESTS=1."""
    return (env("RUN_E2E_TESTS") or "").strip() == "1"


def skip_unless_e2e() -> None:
    """Skip the current test unless E2E is explicitly enabled."""
    if not e2e_enabled():
        pytest.skip("Set RUN_E2E_TESTS=1 to run E2E tests.")


def default_timeout() -> float:
    try:
        return float(env("E2E_TIMEOUT", "120") or "120")
    except Exception:
        return 120.0


def repo_root() -> Path:
    """Best-effort repository root (two levels up from this file)."""
    return Path(__file__).resolve().parents[2]


# --------------------------------- networking --------------------------------


def random_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_port(
    host: str, port: int, *, timeout: float | None = None, interval: float = 0.25
) -> None:
    """Wait until a TCP port accepts connections."""
    deadline = time.time() + (timeout or default_timeout())
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return
        except Exception as exc:  # pragma: no cover - timing dependent
            last_err = exc
            time.sleep(interval)
    raise TimeoutError(
        f"Timed out waiting for {host}:{port} to accept connections; last error: {last_err!r}"
    )


def wait_for_http_ok(
    url: str, *, timeout: float | None = None, interval: float = 0.5
) -> Dict[str, Any]:
    """
    Poll a URL until it returns HTTP 200 and JSON decodes (returns parsed JSON or empty dict).
    If body is not JSON, returns {} on success.
    """
    dl = time.time() + (timeout or default_timeout())
    last_err: Optional[Exception] = None
    while time.time() < dl:
        try:
            req = urllib.request.Request(
                url, headers={"Accept": "application/json"}, method="GET"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    raw = resp.read()
                    try:
                        return json.loads(raw.decode("utf-8"))
                    except Exception:
                        return {}
        except Exception as exc:  # pragma: no cover - timing dependent
            last_err = exc
            time.sleep(interval)
    raise TimeoutError(
        f"Timed out waiting for HTTP 200 at {url}; last error: {last_err!r}"
    )


# --------------------------------- processes ---------------------------------


@dataclass
class ProcSpec:
    cmd: Sequence[str]
    cwd: Optional[Path] = None
    env: Optional[Mapping[str, str]] = None
    wait_for: Optional[Tuple[str, int]] = None  # ("tcp", port) reserved for future


@contextlib.contextmanager
def started_process(spec: ProcSpec) -> Iterator[subprocess.Popen]:
    """
    Start a long-running process for the duration of the context and terminate it on exit.
    Raises if the process exits early with a non-zero code.
    """
    proc_env = os.environ.copy()
    if spec.env:
        proc_env.update(spec.env)
    proc = subprocess.Popen(
        list(spec.cmd),
        cwd=str(spec.cwd) if spec.cwd else None,
        env=proc_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Give it a moment to crash if it's going to
        time.sleep(0.2)
        if proc.poll() is not None and proc.returncode not in (0, None):
            out, err = proc.communicate(timeout=1)
            raise RuntimeError(
                f"Process {spec.cmd} exited early with code {proc.returncode}\nSTDOUT:\n{out}\nSTDERR:\n{err}"
            )
        yield proc
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # pragma: no cover
            with contextlib.suppress(Exception):
                proc.kill()


def run(
    cmd: Sequence[str],
    *,
    timeout: Optional[float] = None,
    check: bool = True,
    cwd: Optional[Path] = None,
    env_extra: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run a command and capture output; raises on non-zero if check=True."""
    proc_env = os.environ.copy()
    if env_extra:
        proc_env.update(env_extra)
    cp = subprocess.run(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=proc_env,
        timeout=timeout or default_timeout(),
        check=False,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(
            f"Command failed ({cp.returncode}): {' '.join(cmd)}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    return cp


# -------------------------------- assertions ---------------------------------


def assert_hex(s: Any, *, prefix: str = "0x", min_nibbles: int = 2) -> None:
    """Assert string is hex-like with given prefix and min length."""
    assert (
        isinstance(s, str)
        and s.startswith(prefix)
        and len(s) >= len(prefix) + min_nibbles
    )
    int(s[len(prefix) :], 16)  # will raise if not hex


def require_cmd(binary: str) -> None:
    """Skip if an external binary is not available on PATH."""
    from shutil import which

    if which(binary) is None:
        pytest.skip(f"Required binary not found on PATH: {binary}")


# ------------------------------- exports -------------------------------------

__all__ = [
    "env",
    "e2e_enabled",
    "skip_unless_e2e",
    "default_timeout",
    "repo_root",
    "random_free_port",
    "wait_for_port",
    "wait_for_http_ok",
    "ProcSpec",
    "started_process",
    "run",
    "assert_hex",
    "require_cmd",
]
