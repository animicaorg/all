"""StudioWorker — the fleet side of Animica Studio.

This is what turns a miner/trainer rig into a Studio function provider: it polls
the AICF broker for ``function_compute`` jobs, runs each one in a local sandbox
(the same `_wrapper` the SDK uses), and submits the packed result. It's the
Python sibling of the provider-daemon TypeScript ``function`` adapter, and the
component that ``animica up`` launches so the *same* hardware that mines and
trains also serves serverless functions — all bound to one ANM payout address.

Run standalone:  ``animica studio worker --address anim1...``
Run in the fleet: enabled automatically by ``animica up`` (see animica.unified).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Optional, Tuple

from . import serialize
from .client import BrokerClient
from .config import StudioConfig

FUNCTION_CLASS = "function_compute"


def execute_spec(call_spec: dict, *, timeout: int, env: Optional[dict] = None) -> Tuple[dict, dict]:
    """Run a packed call spec in a subprocess sandbox.

    Returns ``(packed_result, usage)`` on success; raises ``RuntimeError`` with
    the remote traceback on a function failure or a missing result.
    """
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    started = time.time()
    blob = serialize.dumps(call_spec)
    with tempfile.NamedTemporaryFile("w", suffix=".studio.json", delete=False) as fh:
        fh.write(blob)
        spec_path = fh.name
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "animica.studio._wrapper", spec_path],
            capture_output=True, text=True, timeout=timeout, env=run_env,
        )
    finally:
        try:
            os.unlink(spec_path)
        except OSError:
            pass

    result_line = error_line = None
    out_len = len(proc.stdout or "")
    for line in (proc.stdout or "").splitlines():
        if line.startswith(serialize.RESULT_SENTINEL):
            result_line = line[len(serialize.RESULT_SENTINEL):]
        elif line.startswith(serialize.ERROR_SENTINEL):
            error_line = line[len(serialize.ERROR_SENTINEL):]

    usage = {
        "inputTokens": 0,
        "outputTokens": 0,
        "bytesIn": len(blob),
        "bytesOut": out_len,
        "latencyMs": int((time.time() - started) * 1000),
    }
    if error_line is not None:
        payload = json.loads(serialize.b64decode(error_line).decode("utf-8"))
        raise RuntimeError(f"{payload.get('message')}\n{payload.get('traceback', '')}")
    if result_line is not None:
        packed = json.loads(serialize.b64decode(result_line).decode("utf-8"))
        return packed, usage
    raise RuntimeError(f"no result (exit={proc.returncode}): {(proc.stderr or '')[-400:]}")


class StudioWorker:
    def __init__(
        self,
        config: StudioConfig,
        *,
        address: str,
        tiers: Optional[list] = None,
        poll_interval: float = 2.0,
        gpu: bool = False,
    ):
        if not address:
            raise ValueError("StudioWorker needs an ANM payout address")
        self.config = config
        self.client = BrokerClient(config.rpc_url)
        self.address = address
        self.tiers = tiers or [FUNCTION_CLASS]
        self.poll_interval = poll_interval
        self.gpu = gpu
        self._stop = False
        self._jobs_done = 0

    def stop(self) -> None:
        self._stop = True

    def register(self) -> None:
        try:
            self.client.worker_register(
                self.address, self.tiers,
                hardware={"gpu": self.gpu}, capabilities=[FUNCTION_CLASS],
            )
        except Exception as exc:  # noqa: BLE001 - registration is best-effort
            print(f"[studio-worker] register failed (continuing): {exc}", file=sys.stderr)

    def run_forever(self) -> None:
        self.register()
        print(f"[studio-worker] serving {FUNCTION_CLASS} as {self.address} "
              f"(rpc={self.config.rpc_url})", file=sys.stderr)
        backoff = self.poll_interval
        while not self._stop:
            job = None
            try:
                job = self.client.worker_claim(self.address, self.tiers)
            except Exception as exc:  # noqa: BLE001 - broker may be down; keep trying
                print(f"[studio-worker] claim error: {exc}", file=sys.stderr)
            if not job or not job.get("job_id"):
                time.sleep(min(backoff, 10.0))
                backoff = min(backoff * 1.2, 10.0)
                continue
            backoff = self.poll_interval
            self._handle(job)

    def _handle(self, job: dict) -> None:
        job_id = job["job_id"]
        spec = job.get("spec") or {}
        if spec.get("class") != FUNCTION_CLASS:
            return  # not a Studio job; leave it for the right worker
        call = (spec.get("input") or {}).get("call")
        timeout = int(spec.get("timeoutSeconds") or self.config.default_timeout_s)
        try:
            packed, usage = execute_spec(call, timeout=timeout)
            text = serialize.RESULT_SENTINEL + serialize.b64encode(
                json.dumps(packed).encode("utf-8")
            )
            self.client.worker_submit_result(self.address, job_id, text, usage=usage)
            self._jobs_done += 1
            print(f"[studio-worker] completed {job_id} ({self._jobs_done} total)", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - report the failure to the broker
            try:
                self.client.worker_submit_failure(self.address, job_id, str(exc)[:500])
            except Exception:
                pass
            print(f"[studio-worker] failed {job_id}: {exc}", file=sys.stderr)
