"""
animica.ena.worker
==================

The ENA contributor worker loop. A machine repeatedly claims useful-work jobs,
executes them locally, verifies, and emits a receipt + on-chain export envelope
(the credit-event that backs an ANM payout). This is how an ordinary CPU miner
contributes to the ENA training flywheel and earns.

Two transports:

* **local**  — drives an in-process :class:`~animica.ena.ENA` over the local
  SQLite store (single node / dev / self-hosted coordinator on the same box).
* **remote** — talks HTTP to an ``animica ena serve`` coordinator so a fleet of
  machines can pull from one shared queue.

GPU operators run full SFT/DPO/distill via ``ena train run`` and anchor the
result with :func:`animica.ena.training.build_training_receipt`; this loop
covers the CPU-friendly job kinds every contributor can run.
"""

from __future__ import annotations

import json
import signal
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .models import now_ts


def _local_model_adapter():
    """Build a model adapter pointed at THIS worker's local runtime.

    Resolution (all optional, with safe fallbacks):
      * base url   — LOCAL_INFERENCE_URL or ANIMICA_ENA_MODEL_BASE_URL
      * model name — LOCAL_INFERENCE_MODEL or ANIMICA_ENA_MODEL_NAME
      * adapter    — ANIMICA_ENA_MODEL_ADAPTER, else inferred (a ``…/v1`` base is
                     OpenAI-compatible, otherwise Ollama)
      * timeout    — ANIMICA_ENA_MODEL_TIMEOUT (default 600s; generous because
                     it's the miner's own hardware, not a shared coordinator)

    With no local model configured this degrades to the deterministic adapter so
    the worker never crashes — it just produces zero usable pairs for that job.
    """
    import os
    from .models import ModelProviderConfig
    from .providers import build_model_adapter

    base = (os.environ.get("LOCAL_INFERENCE_URL")
            or os.environ.get("ANIMICA_ENA_MODEL_BASE_URL") or "")
    model = (os.environ.get("LOCAL_INFERENCE_MODEL")
             or os.environ.get("ANIMICA_ENA_MODEL_NAME") or "")
    adapter = os.environ.get("ANIMICA_ENA_MODEL_ADAPTER") or ""
    if not base or not model:
        cfg = ModelProviderConfig(name="local-miner", provider="deterministic",
                                  transport="fallback", model="deterministic")
        return build_model_adapter(cfg)
    if not adapter:
        adapter = "openai_compatible" if base.rstrip("/").endswith("/v1") else "ollama"
    timeout = float(os.environ.get("ANIMICA_ENA_MODEL_TIMEOUT", "600"))
    cfg = ModelProviderConfig(
        name="local-miner", provider=adapter, transport="local_runtime",
        model=model, base_url=base, timeout_seconds=timeout,
        max_tokens=int(os.environ.get("ANIMICA_ENA_MODEL_MAX_TOKENS", "768")))
    return build_model_adapter(cfg)


@dataclass
class WorkerStats:
    worker_id: str
    started_at: int = field(default_factory=now_ts)
    jobs_completed: int = 0
    jobs_failed: int = 0
    jobs_rejected: int = 0
    earnings_anm: float = 0.0
    last_job_id: Optional[str] = None
    last_activity_at: int = 0


class _RemoteClient:
    def __init__(self, endpoint: str, timeout: float = 30.0) -> None:
        self.base = endpoint.rstrip("/")
        self.timeout = timeout

    def _call(self, path: str, method: str = "GET",
              body: Optional[dict] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"cannot reach {self.base}: {exc}") from exc

    def claim(self, worker_id: str, types: Optional[list[str]]) -> Optional[dict]:
        return self._call("/jobs/claim", "POST",
                          {"worker_id": worker_id, "types": types})

    def run(self, job_id: str) -> dict:
        return self._call(f"/jobs/{job_id}/run", "POST", {})

    def submit_result(self, job_id: str, result: dict,
                      worker_id: Optional[str] = None) -> dict:
        return self._call(f"/jobs/{job_id}/submit-result", "POST",
                          {"result": result, "worker_id": worker_id})

    def verify(self, job_id: str) -> dict:
        return self._call(f"/jobs/{job_id}/verify", "POST", {})

    def receipt(self, job_id: str) -> dict:
        return self._call(f"/jobs/{job_id}/receipt", "POST", {})

    def export(self, job_id: str) -> dict:
        return self._call(f"/jobs/{job_id}/export", "POST", {})


class ENAWorker:
    def __init__(self, *, worker_id: str, ena=None, endpoint: Optional[str] = None,
                 types: Optional[list[str]] = None, poll_interval: float = 3.0,
                 state_path: Optional[str] = None) -> None:
        if (ena is None) == (endpoint is None):
            raise ValueError("provide exactly one of ena (local) or endpoint (remote)")
        self.worker_id = worker_id
        self.ena = ena
        self.remote = _RemoteClient(endpoint) if endpoint else None
        self.types = types
        self.poll_interval = poll_interval
        self.stats = WorkerStats(worker_id=worker_id)
        self._stop = False
        self.state_path = Path(state_path) if state_path else (
            Path("~/.animica/ena/worker-state.json").expanduser())

    # -- transport-agnostic ops ------------------------------------------
    def _claim(self) -> Optional[dict]:
        if self.remote:
            return self.remote.claim(self.worker_id, self.types)
        return self.ena.jobs.claim(self.worker_id, self.types)

    def _process(self, job: dict) -> dict:
        from .jobs import WORKER_LOCAL_JOB_TYPES
        job_id = job["job_id"]
        if job.get("job_type") in WORKER_LOCAL_JOB_TYPES:
            return self._process_worker_local(job)
        if self.remote:
            self.remote.run(job_id)
            self.remote.verify(job_id)
            self.remote.receipt(job_id)
            return self.remote.export(job_id)
        self.ena.jobs.run(job_id, worker_id=self.worker_id)
        self.ena.jobs.verify(job_id)
        self.ena.jobs.receipt(job_id)
        return self.ena.jobs.export_onchain(job_id)

    def _process_worker_local(self, job: dict) -> dict:
        """Run a model-backed job on THIS worker's local model, then return the
        result to the coordinator (remote) or finalize in-process (local).

        The LLM call happens here — on the miner's own GPU/ollama behind
        LOCAL_INFERENCE_URL — so it can take as long as the hardware needs
        without tripping the coordinator's CPU or the worker's HTTP read timeout
        (we hold no request open during generation). The coordinator only sees a
        fast ``submit-result`` POST and then verifies/receipts the stored result.
        """
        job_id = job["job_id"]
        if not self.remote:
            # Single-box / self-hosted: execute against this node's own model, then
            # land the result via submit_result so the synthesized pairs are curated
            # into STAGING here too (same DNA-growth path as the remote fleet).
            done = self.ena.jobs.run(job_id, worker_id=self.worker_id,
                                     allow_worker_local=True)
            self.ena.jobs.submit_result(job_id, result=done.get("result"),
                                        worker_id=self.worker_id)
            self.ena.jobs.verify(job_id)
            self.ena.jobs.receipt(job_id)
            return self.ena.jobs.export_onchain(job_id)
        # Remote fleet worker: generate locally, submit the result, then let the
        # coordinator verify/receipt/export over fast calls.
        from . import synth
        params = job.get("params") or {}
        corpus = params.get("corpus") or params.get("source_chunk") or ""
        model = _local_model_adapter()
        pairs, meta = synth.synthesize(
            model, str(corpus),
            num_pairs=int(params.get("num_pairs", synth.DEFAULT_NUM_PAIRS)),
            max_tokens=int(params.get("max_tokens", synth.DEFAULT_MAX_TOKENS)),
            temperature=float(params.get("temperature", synth.DEFAULT_TEMPERATURE)))
        result = {"pairs": pairs, "pair_count": len(pairs), **meta}
        self.remote.submit_result(job_id, result, worker_id=self.worker_id)
        self.remote.verify(job_id)
        self.remote.receipt(job_id)
        return self.remote.export(job_id)

    # -- loop -------------------------------------------------------------
    def tick(self) -> Optional[dict]:
        """Claim + process one job. Returns the export envelope, or None if idle."""
        job = self._claim()
        if not job:
            return None
        self.stats.last_job_id = job["job_id"]
        self.stats.last_activity_at = now_ts()
        try:
            envelope = self._process(job)
        except Exception as exc:  # noqa: BLE001
            self.stats.jobs_failed += 1
            self._write_state()
            raise RuntimeError(f"job {job['job_id']} failed: {exc}") from exc
        valid = bool((envelope or {}).get("validation", {}).get("valid"))
        if valid:
            self.stats.jobs_completed += 1
            try:
                self.stats.earnings_anm += float(
                    (envelope.get("credit_event") or {}).get("reward", 0) or 0)
            except (TypeError, ValueError):
                pass
        else:
            self.stats.jobs_rejected += 1
        self._write_state()
        return envelope

    def run_forever(self, max_jobs: Optional[int] = None,
                    on_event=None) -> WorkerStats:
        self._install_signals()
        done = 0
        while not self._stop:
            try:
                env = self.tick()
            except RuntimeError as exc:
                if on_event:
                    on_event("error", {"error": str(exc)})
                env = None
            if env is None:
                time.sleep(self.poll_interval)
                continue
            done += 1
            if on_event:
                on_event("completed", {"job_id": self.stats.last_job_id,
                                       "earnings_anm": self.stats.earnings_anm})
            if max_jobs and done >= max_jobs:
                break
        return self.stats

    def stop(self) -> None:
        self._stop = True

    def _install_signals(self) -> None:
        def _handler(*_a):
            self._stop = True
        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
        except (ValueError, OSError):  # not on main thread
            pass

    def _write_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(asdict(self.stats), indent=2),
                                       encoding="utf-8")
        except OSError:
            pass
