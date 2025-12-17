from __future__ import annotations

import asyncio
import logging
import math
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .job import JobConfig

try:
    from .mining.gpu_opencl import OpenCLBackend
except Exception:  # pragma: no cover - imported lazily for extraction
    OpenCLBackend = None

log = logging.getLogger("opencl_miner.scanner")


@dataclass
class ShareCandidate:
    job: JobConfig
    nonce: int
    u: float
    d_ratio: float

    @property
    def h_micro(self) -> int:
        return int(max(-math.log(max(self.u, 1e-38)), 0.0) * 1_000_000)


class OpenCLScanner(threading.Thread):
    """
    Dedicated worker thread that keeps the OpenCL context alive and scans
    nonce ranges for the currently active job.
    """

    def __init__(
        self,
        result_queue: "asyncio.Queue[ShareCandidate]",
        *,
        loop: asyncio.AbstractEventLoop,
        iterations: int = 50_000_000,
        max_found: int = 4,
        device_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(name="opencl-scanner", daemon=True)
        if OpenCLBackend is None:
            raise RuntimeError("PyOpenCL backend is not available in this environment.")
        self._loop = loop
        self._results = result_queue
        self._iterations = max(1, int(iterations))
        self._max_found = max(1, int(max_found))
        self._device_kwargs = device_kwargs or {}

        self._cmd_queue: "queue.Queue[Tuple[str, Optional[JobConfig]]]" = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._startup_error: Optional[Exception] = None
        self._hashes_done = 0
        self.device_info: Optional[str] = None

    @property
    def hashes_processed(self) -> int:
        return self._hashes_done

    def stop(self) -> None:
        self._cmd_queue.put(("stop", None))
        self._stop.set()

    def wait_ready(self, timeout: float = 10.0) -> None:
        if not self._ready.wait(timeout):
            raise RuntimeError("OpenCL scanner did not start within timeout.")
        if self._startup_error:
            raise self._startup_error

    def set_job(self, job: JobConfig) -> None:
        self._cmd_queue.put(("job", job))

    # Internal helpers -------------------------------------------------

    def _push_results(self, job: JobConfig, shares: Iterable[Dict[str, Any]]) -> None:
        for share in shares:
            candidate = ShareCandidate(
                job=job,
                nonce=int(share["nonce"]),
                u=float(share["u"]),
                d_ratio=float(share["d_ratio"]),
            )
            self._loop.call_soon_threadsafe(self._results.put_nowait, candidate)

    def run(self) -> None:  # pragma: no cover - threading
        try:
            device = OpenCLBackend(**self._device_kwargs)
            info = getattr(device, "info", None)
            if callable(info):
                self.device_info = str(info())
        except Exception as exc:
            self._startup_error = exc
            self._ready.set()
            return

        self._ready.set()
        current_job: Optional[JobConfig] = None
        prepared = None
        nonce_offset = 0

        while not self._stop.is_set():
            # Drain commands non-blocking
            drained = False
            while True:
                try:
                    cmd, payload = self._cmd_queue.get_nowait()
                except queue.Empty:
                    break
                drained = True
                if cmd == "stop":
                    self._stop.set()
                    break
                if cmd == "job" and payload is not None:
                    current_job = payload
                    prepared = device.prepare_header(
                        current_job.header_bytes, current_job.mix_seed
                    )
                    nonce_offset = 0
                    log.info(
                        "scanner loaded job %s height=%s theta=%s shareTarget=%.6f",
                        current_job.job_id,
                        current_job.height,
                        current_job.theta_micro,
                        current_job.share_target,
                    )
            if self._stop.is_set():
                break
            if current_job is None or prepared is None:
                time.sleep(0.05)
                continue

            try:
                shares = device.scan(
                    prepared,
                    theta_micro=current_job.theta_share_micro,
                    start_nonce=nonce_offset,
                    iterations=self._iterations,
                    max_found=self._max_found,
                )
                self._hashes_done += self._iterations
                nonce_offset = (nonce_offset + self._iterations) & 0xFFFFFFFFFFFFFFFF
                if shares:
                    self._push_results(current_job, shares)
            except Exception as exc:
                log.error("OpenCL scan failed: %s", exc, exc_info=True)
                time.sleep(0.5)

        try:
            device.close()
        except Exception:
            pass
