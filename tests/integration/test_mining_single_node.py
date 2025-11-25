# -*- coding: utf-8 -*-
"""
Integration: built-in miner can find dev blocks against a running node.

This test is intentionally black-box and **skips by default** (see
tests/integration/__init__.py gate). It assumes you already have a devnet node
running and reachable via JSON-RPC.

What it does (when enabled):
  1) Records the current chain head height via JSON-RPC.
  2) Starts the built-in CPU miner as a subprocess (command provided by env).
  3) Polls the head until it advances by a configured number of blocks within
     a timeout.
  4) Shuts the miner down and reports basic diagnostics.

Environment variables (all optional except ANIMICA_MINER_CMD):
  RUN_INTEGRATION_TESTS=1     — enable integration tests package-wide

  ANIMICA_RPC_URL             — JSON-RPC URL (default: http://127.0.0.1:8545)
  ANIMICA_HTTP_TIMEOUT        — single RPC call timeout in seconds (default: 5)

  ANIMICA_MINER_CMD           — full command to launch the miner subprocess.
                                Example:
                                  "python -m mining.cli.miner start --threads 1 --device cpu"
                                If not provided, this test is skipped.
  ANIMICA_MINER_TIMEOUT       — total time to allow mining (default: 120 seconds)
  ANIMICA_MINER_EXPECT_ADV    — required head delta to pass (default: 2 blocks)
  ANIMICA_MINER_WARMUP        — seconds to wait before first poll (default: 2.0)
  ANIMICA_HEAD_POLL_INTERVAL  — poll interval for head checks (default: 1.0)

Notes:
  * We don't try to infer CLI flags; pass whatever your miner needs via
    ANIMICA_MINER_CMD (it should point the miner at the same RPC target).
  * The test captures miner stdout/stderr for debugging on failure.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # package-level RUN_INTEGRATION_TESTS gate lives there


# -------------------------------- RPC helpers --------------------------------

def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(rpc_url: str, method: str, params: Optional[Sequence[Any] | Dict[str, Any]] = None, *, req_id: int = 1) -> Any:
    if params is None:
        params = []
    if isinstance(params, dict):
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    else:
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": list(params)}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"JSON-RPC error from {method}: {msg['error']}")
    return msg.get("result")


def _rpc_try(rpc_url: str, methods: Sequence[str], params: Optional[Sequence[Any] | Dict[str, Any]] = None) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:
            last_exc = exc
            continue
    raise AssertionError(f"All RPC spellings failed ({methods}). Last error: {last_exc}")


def _parse_height(head: Any) -> int:
    if isinstance(head, dict):
        for k in ("height", "number", "index"):
            v = head.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                try:
                    return int(v, 0)
                except Exception:
                    pass
    raise AssertionError(f"Unrecognized head shape: {head!r}")


# ------------------------------- Miner helpers -------------------------------

class MinerProc:
    def __init__(self, cmdline: str):
        self.cmdline = cmdline
        self.proc: Optional[subprocess.Popen] = None
        self._stdout_buf: list[str] = []
        self._stderr_buf: list[str] = []

    def start(self) -> None:
        # Use shell-style splitting so ANIMICA_MINER_CMD can be a single string
        argv = shlex.split(self.cmdline)
        # Start process in its own group so we can tear it down cleanly
        kwargs = {}
        if os.name != "nt":
            kwargs["preexec_fn"] = os.setsid  # type: ignore[attr-defined]
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffer
            **kwargs,
        )

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def read_nonblocking(self) -> None:
        if not self.proc:
            return
        # Drain available output (non-blocking because pipes are in text mode + bufsize=1)
        for stream, buf in ((self.proc.stdout, self._stdout_buf), (self.proc.stderr, self._stderr_buf)):
            if stream is None:
                continue
            while True:
                try:
                    line = stream.readline()
                except Exception:
                    break
                if not line:
                    break
                buf.append(line.rstrip())

    def stop(self, timeout: float = 5.0) -> None:
        if not self.proc:
            return
        self.read_nonblocking()
        if os.name != "nt":
            # Kill the whole process group
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)  # type: ignore[arg-type]
            except Exception:
                pass
        else:
            try:
                self.proc.terminate()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=timeout)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.read_nonblocking()

    def logs(self, tail: int = 200) -> str:
        out = "\n".join(self._stdout_buf[-tail:])
        err = "\n".join(self._stderr_buf[-tail:])
        return f"--- miner stdout (tail) ---\n{out}\n--- miner stderr (tail) ---\n{err}\n"


@pytest.mark.timeout(240)
def test_built_in_miner_finds_blocks_and_head_advances():
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    miner_cmd = env("ANIMICA_MINER_CMD")  # REQUIRED
    if not miner_cmd:
        pytest.skip("ANIMICA_MINER_CMD not set; provide a full miner command (e.g., 'python -m mining.cli.miner start --threads 1 --device cpu')")

    # Baseline head
    _, head0 = _rpc_try(rpc_url, ("chain.getHead", "chain.head", "getHead"), [])
    h0 = _parse_height(head0)

    expect_adv = int(env("ANIMICA_MINER_EXPECT_ADV", "2") or "2")
    timeout_s = float(env("ANIMICA_MINER_TIMEOUT", "120") or "120")
    warmup_s = float(env("ANIMICA_MINER_WARMUP", "2.0") or "2.0")
    interval_s = float(env("ANIMICA_HEAD_POLL_INTERVAL", "1.0") or "1.0")

    miner = MinerProc(miner_cmd)
    miner.start()
    try:
        # Give the miner a moment to boot and fetch templates
        time.sleep(warmup_s)

        deadline = time.time() + timeout_s
        last_h = h0
        advanced_by = 0

        while time.time() < deadline:
            miner.read_nonblocking()
            _, head = _rpc_try(rpc_url, ("chain.getHead", "chain.head", "getHead"), [])
            h = _parse_height(head)
            if h > last_h:
                advanced_by = h - h0
                if advanced_by >= expect_adv:
                    break
                last_h = h
            time.sleep(interval_s)

        miner.read_nonblocking()

        # Assert success
        if advanced_by < expect_adv:
            pytest.fail(
                f"Head did not advance by {expect_adv} blocks within {timeout_s:.1f}s "
                f"(start {h0}, last {last_h}, advanced {advanced_by}).\n{miner.logs()}"
            )

    finally:
        miner.stop(timeout=5.0)

    # Bonus: print brief miner logs on success for context (not an assertion)
    sys.stdout.write(miner.logs(tail=40))
    sys.stdout.flush()
