from __future__ import annotations

"""
AICFQueueAdapter
================

Thin, resilient bridge from the miner to the AI Compute Fund (AICF) queue.

Goals
-----
- **Enqueue** tiny AI / Quantum jobs during devnet demos (used by mining/ai_worker.py
  and mining/quantum_worker.py).
- **Throttle** submissions (token-bucket) so local miners don't spam the queue.
- **SLA awareness**: prefer healthy providers based on recent outcomes and (optionally)
  AICF-reported provider health.

Integration Strategy
--------------------
We try progressively, in this order:

1) Local Python integration (preferred)
   - Import `capabilities.adapters.aicf` (ships in this repo).
   - We permissively probe one of:
        enqueue_job(kind, spec) → job_id
        enqueue_ai(spec) / enqueue_quantum(spec) → job_id
        get_job(job_id) → dict with status/result/provider info

2) JSON-RPC fallback (optional)
   - If env AICF_RPC_URL is set, we POST JSON-RPC calls to the main node's RPC
     (AICF endpoints are mounted by aicf/rpc/mount.py). We use:
       aicf.listProviders, aicf.getProvider, aicf.listJobs, aicf.getJob
     (Note: enqueue is usually via `capabilities` path; miners rarely enqueue
      directly to AICF over RPC. We keep RPC read-only by default.)

3) No-op DEV queue
   - As a last resort for offline runs, we accept the job locally and return
     a deterministic dummy job_id; `get_job` will report QUEUED forever.

This adapter **does not** do consensus mapping of results; it only submits
and polls. Once the proof is assembled by the worker, it will be verified and
mapped to ψ-inputs by `mining/adapters/proofs_view.py`.

Thread-safety
-------------
Lightly synchronized via per-kind token buckets and one in-memory inflight set.
This is sufficient for the default miner orchestrator (single process).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Callable
import os
import time
import json
import hashlib
import threading

# -----------------------------------------------------------------------------
# Optional logging
try:
    from core.logging import get_logger
    log = get_logger("mining.adapters.aicf_queue")
except Exception:  # noqa: BLE001
    import logging
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("mining.adapters.aicf_queue")

# -----------------------------------------------------------------------------
# Try local capabilities adapter (preferred path in this repo)
_cap_aicf: Optional[Any] = None
try:
    import capabilities.adapters.aicf as _cap_aicf  # type: ignore[assignment]
except Exception:  # noqa: BLE001
    _cap_aicf = None

# -----------------------------------------------------------------------------
# Optional HTTP client for JSON-RPC fallback
_http_post: Optional[Callable[[str, bytes, Dict[str, str]], Tuple[int, bytes]]] = None
try:
    import requests  # type: ignore

    def _http_post(url: str, body: bytes, headers: Dict[str, str]) -> Tuple[int, bytes]:  # type: ignore[no-redef]
        r = requests.post(url, data=body, headers=headers, timeout=5)
        return r.status_code, r.content
except Exception:  # noqa: BLE001
    try:
        import urllib.request  # type: ignore

        def _http_post(url: str, body: bytes, headers: Dict[str, str]) -> Tuple[int, bytes]:  # type: ignore[no-redef]
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 (local dev)
                return resp.getcode(), resp.read()
    except Exception:  # noqa: BLE001
        _http_post = None

# -----------------------------------------------------------------------------
# Types

@dataclass
class TokenBucket:
    """Simple token-bucket rate limiter (thread-safe)."""
    capacity: float
    refill_per_sec: float
    tokens: float = field(default=0.0)
    last: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self.lock:
            elapsed = now - self.last
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self.last = now
            if self.tokens >= cost:
                self.tokens -= cost
                return True
            return False


@dataclass(frozen=True)
class JobId:
    value: str  # hex or bech32-ish string from the underlying system


@dataclass
class EnqueueResult:
    ok: bool
    job_id: Optional[JobId]
    reason: Optional[str] = None


@dataclass
class JobStatus:
    job_id: JobId
    status: str               # "QUEUED" | "ASSIGNED" | "RUNNING" | "COMPLETED" | "FAILED" | "EXPIRED"
    provider_id: Optional[str] = None
    result_digest: Optional[str] = None
    error: Optional[str] = None
    # Optional SLA-related measures (if exposed)
    traps_ratio: Optional[float] = None
    qos: Optional[float] = None
    latency_ms: Optional[int] = None


# -----------------------------------------------------------------------------
# Adapter

class AICFQueueAdapter:
    """
    Submit/poll AI & Quantum jobs with throttling and SLA awareness.

    Parameters
    ----------
    rpc_url : Optional[str]
        If provided (or env AICF_RPC_URL is set), enables JSON-RPC fallback for read-only queries.
    ai_rps : float
        Max average AI job *submissions* per second (token bucket).
    q_rps : float
        Max average Quantum job submissions per second (token bucket).
    max_inflight : int
        Upper bound on concurrently "inflight" jobs we allow this adapter to hold.
    """

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        *,
        ai_rps: float = 2.0,
        q_rps: float = 0.5,
        max_inflight: int = 16,
    ) -> None:
        self.rpc_url = rpc_url or os.getenv("AICF_RPC_URL") or ""
        self._use_rpc = bool(self.rpc_url and _http_post is not None)

        # Token buckets
        self._tb_ai = TokenBucket(capacity=4.0, refill_per_sec=ai_rps)
        self._tb_q = TokenBucket(capacity=2.0, refill_per_sec=q_rps)

        # Inflight accounting
        self._inflight_lock = threading.Lock()
        self._inflight: set[str] = set()
        self._max_inflight = max_inflight

        # Simple in-memory SLA tracker (EMA)
        self._ema_alpha = 0.2
        self._provider_ok: Dict[str, float] = {}   # provider_id → success-rate EMA
        self._provider_rtt: Dict[str, float] = {}  # provider_id → latency EMA (ms)

        if _cap_aicf is not None:
            log.info("AICFQueueAdapter: using local capabilities.adapters.aicf")
        elif self._use_rpc:
            log.info("AICFQueueAdapter: JSON-RPC fallback enabled for read-only methods")
        else:
            log.warning("AICFQueueAdapter: no AICF backend available; using noop DEV queue")

    # ---------------------- Public API ----------------------

    def enqueue_ai(
        self,
        *,
        model: str,
        prompt: Any,
        max_cost_units: int = 64,
        priority: int = 0,
        prefer_providers: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EnqueueResult:
        """Enqueue a small AI job."""
        if not self._tb_ai.allow(1.0):
            return EnqueueResult(ok=False, job_id=None, reason="throttled-ai-rps")

        if not self._reserve_inflight():
            return EnqueueResult(ok=False, job_id=None, reason="too-many-inflight")

        spec = {
            "kind": "AI",
            "model": model,
            "prompt": prompt,
            "max_cost_units": int(max_cost_units),
            "priority": int(priority),
            "prefer_providers": prefer_providers or self._ranked_providers(kind="AI"),
            "metadata": metadata or {},
        }
        try:
            job_id = self._submit_via_local_or_noop(spec, prefer=("ai",))
            if job_id is None:
                return EnqueueResult(ok=False, job_id=None, reason="enqueue-failed")
            return EnqueueResult(ok=True, job_id=JobId(job_id))
        except Exception as e:  # noqa: BLE001
            log.warning("enqueue_ai failed", extra={"err": str(e)})
            self._release_inflight_placeholder(job_id=None)
            return EnqueueResult(ok=False, job_id=None, reason=str(e))

    def enqueue_quantum(
        self,
        *,
        circuit: Dict[str, Any],
        shots: int,
        max_cost_units: int = 128,
        priority: int = 0,
        prefer_providers: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EnqueueResult:
        """Enqueue a small Quantum job (trap-compatible circuits for devnet)."""
        if not self._tb_q.allow(1.0):
            return EnqueueResult(ok=False, job_id=None, reason="throttled-quantum-rps")

        if not self._reserve_inflight():
            return EnqueueResult(ok=False, job_id=None, reason="too-many-inflight")

        spec = {
            "kind": "QUANTUM",
            "circuit": circuit,
            "shots": int(shots),
            "max_cost_units": int(max_cost_units),
            "priority": int(priority),
            "prefer_providers": prefer_providers or self._ranked_providers(kind="QUANTUM"),
            "metadata": metadata or {},
        }
        try:
            job_id = self._submit_via_local_or_noop(spec, prefer=("quantum",))
            if job_id is None:
                return EnqueueResult(ok=False, job_id=None, reason="enqueue-failed")
            return EnqueueResult(ok=True, job_id=JobId(job_id))
        except Exception as e:  # noqa: BLE001
            log.warning("enqueue_quantum failed", extra={"err": str(e)})
            self._release_inflight_placeholder(job_id=None)
            return EnqueueResult(ok=False, job_id=None, reason=str(e))

    def get_job(self, job_id: JobId) -> JobStatus:
        """
        Fetch job status (local capabilities adapter preferred).
        """
        # Try local capabilities.adapters.aicf
        if _cap_aicf is not None:
            for name in ("get_job", "get_job_status", "get_result", "fetch_job"):
                fn = getattr(_cap_aicf, name, None)
                if callable(fn):
                    info = fn(job_id.value)  # type: ignore[misc]
                    status = self._normalize_status(job_id, info)
                    self._maybe_update_sla(status)
                    self._maybe_release_inflight(status)
                    return status

        # Fallback to JSON-RPC read-only (no enqueue)
        if self._use_rpc:
            try:
                res = self._rpc_call("aicf.getJob", {"jobId": job_id.value})
                status = self._normalize_status(job_id, res.get("result", res))
                self._maybe_update_sla(status)
                self._maybe_release_inflight(status)
                return status
            except Exception as e:  # noqa: BLE001
                log.debug("RPC getJob failed", extra={"err": str(e)})

        # No backend: noop queue → always QUEUED
        return JobStatus(job_id=job_id, status="QUEUED")

    # -------------------- Internals: submission --------------------

    def _submit_via_local_or_noop(self, spec: Dict[str, Any], *, prefer: Tuple[str, ...]) -> Optional[str]:
        """
        Try local adapter first. If not present, fall back to noop dev submission.
        """
        if _cap_aicf is not None:
            # Probe function names in a permissive order
            # Generic
            for name in ("enqueue_job", "submit_job"):
                fn = getattr(_cap_aicf, name, None)
                if callable(fn):
                    jid = fn(spec["kind"], spec)  # type: ignore[misc]
                    self._track_inflight(jid)
                    return str(jid)
            # Kind-specific
            for pfx in prefer:
                for name in (f"enqueue_{pfx}", f"submit_{pfx}", f"enqueue_{pfx}_job"):
                    fn = getattr(_cap_aicf, name, None)
                    if callable(fn):
                        jid = fn(spec)  # type: ignore[misc]
                        self._track_inflight(jid)
                        return str(jid)

        # No backend -> noop deterministic id
        jid = self._make_noop_job_id(spec)
        self._track_inflight(jid)
        return jid

    # -------------------- Internals: status & SLA --------------------

    def _normalize_status(self, job_id: JobId, info: Dict[str, Any]) -> JobStatus:
        status = str(info.get("status", "QUEUED")).upper()
        provider = info.get("provider_id") or info.get("provider") or None
        traps = _to_float(info.get("traps_ratio"))
        qos = _to_float(info.get("qos"))
        latency = _to_int(info.get("latency_ms"))
        result_d = info.get("result_digest") or info.get("result") or None
        if isinstance(result_d, bytes):
            result_d = result_d.hex()

        # Minimal shape normalization
        js = JobStatus(
            job_id=job_id,
            status=status,
            provider_id=str(provider) if provider is not None else None,
            result_digest=str(result_d) if result_d is not None else None,
            error=str(info.get("error")) if info.get("error") else None,
            traps_ratio=traps,
            qos=qos,
            latency_ms=latency,
        )
        return js

    def _maybe_update_sla(self, st: JobStatus) -> None:
        if not st.provider_id:
            return
        pid = st.provider_id
        # success-rate EMA
        ok = 1.0 if st.status == "COMPLETED" else (0.0 if st.status in ("FAILED", "EXPIRED") else None)
        if ok is not None:
            prev = self._provider_ok.get(pid, 0.8)
            self._provider_ok[pid] = prev * (1 - self._ema_alpha) + ok * self._ema_alpha
        # latency EMA
        if st.latency_ms is not None:
            prev = self._provider_rtt.get(pid, float(st.latency_ms))
            self._provider_rtt[pid] = prev * (1 - self._ema_alpha) + float(st.latency_ms) * self._ema_alpha

    def _maybe_release_inflight(self, st: JobStatus) -> None:
        if st.status in ("COMPLETED", "FAILED", "EXPIRED"):
            self._release_inflight_placeholder(job_id=st.job_id.value)

    # -------------------- Internals: providers --------------------

    def _ranked_providers(self, *, kind: str) -> list[str]:
        """
        Return a *hint* list of provider_ids we currently consider healthy.
        Uses (provider_ok descending, then latency ascending). If RPC is available,
        we may pull fresh metadata; otherwise we use local EMA views.
        """
        candidates: list[Tuple[str, float, float]] = []

        if self._use_rpc:
            try:
                res = self._rpc_call("aicf.listProviders", {"kind": kind})
                items = res.get("result", res)
                if isinstance(items, dict) and "items" in items:
                    items = items["items"]
                for it in items or []:
                    pid = str(it.get("id"))
                    ok = float(it.get("ok_ema", self._provider_ok.get(pid, 0.8)))
                    rtt = float(it.get("rtt_ms", self._provider_rtt.get(pid, 200.0)))
                    candidates.append((pid, ok, rtt))
            except Exception as e:  # noqa: BLE001
                log.debug("listProviders via RPC failed", extra={"err": str(e)})

        # Merge in local EMA knowledge
        for pid, ok in list(self._provider_ok.items()):
            rtt = self._provider_rtt.get(pid, 200.0)
            candidates.append((pid, ok, rtt))

        if not candidates:
            return []  # no hint

        # Deduplicate by best (ok desc, rtt asc)
        seen: set[str] = set()
        ranked = sorted(candidates, key=lambda t: (-t[1], t[2]))
        out: list[str] = []
        for pid, _, _ in ranked:
            if pid not in seen:
                out.append(pid)
                seen.add(pid)
        return out[:8]  # cap hint list

    # -------------------- Internals: inflight & throttling --------------------

    def _reserve_inflight(self) -> bool:
        with self._inflight_lock:
            if len(self._inflight) >= self._max_inflight:
                return False
            # placeholder reserved by count; real id tracked on submit
            self._inflight.add(f"__placeholder__:{time.monotonic_ns()}")
            return True

    def _track_inflight(self, job_id: str) -> None:
        with self._inflight_lock:
            # remove one placeholder if present
            for x in list(self._inflight):
                if x.startswith("__placeholder__"):
                    self._inflight.remove(x)
                    break
            self._inflight.add(job_id)

    def _release_inflight_placeholder(self, job_id: Optional[str]) -> None:
        with self._inflight_lock:
            if job_id and job_id in self._inflight:
                self._inflight.remove(job_id)
                return
            # drop any placeholder
            for x in list(self._inflight):
                if x.startswith("__placeholder__"):
                    self._inflight.remove(x)
                    break

    # -------------------- Internals: JSON-RPC --------------------

    def _rpc_call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._use_rpc or not _http_post:
            raise RuntimeError("JSON-RPC not available")
        body = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) & 0x7FFFFFFF,
            "method": method,
            "params": params,
        }
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        url = self.rpc_url.rstrip("/") + "/rpc"
        code, resp = _http_post(url, data, {"content-type": "application/json"})
        if code != 200:
            raise RuntimeError(f"RPC HTTP {code}")
        out = json.loads(resp.decode("utf-8"))
        if "error" in out and out["error"]:
            raise RuntimeError(f"RPC error: {out['error']}")
        return out.get("result", out)

    # -------------------- Internals: NOOP queue --------------------

    def _make_noop_job_id(self, spec: Dict[str, Any]) -> str:
        h = hashlib.sha3_256()
        h.update(b"NOOP-AICF-JOB|")
        h.update(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        h.update(str(time.time_ns()).encode("ascii"))
        return "noop_" + h.hexdigest()[:24]


# -----------------------------------------------------------------------------
# Helpers

def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:  # noqa: BLE001
        return None


def _to_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "AICFQueueAdapter",
    "TokenBucket",
    "JobId",
    "EnqueueResult",
    "JobStatus",
]
