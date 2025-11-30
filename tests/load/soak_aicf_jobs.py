# -*- coding: utf-8 -*-
"""
soak_aicf_jobs.py
==================

Steady **AICF** job enqueue load (AI + Quantum) with SLA-ish monitoring.

This script fires a configurable stream of job requests and tracks:
- enqueue latency (RPC/HTTP round-trip)
- time-to-completion (until a result is observable)
- success/failure rates
- optional provider snapshots via aicf.listProviders (if exposed)

It is backend-agnostic and supports two ways to talk to your stack:

1) JSON-RPC (recommended):
   - Use --rpc-url http://127.0.0.1:8545
   - Methods are configurable; by default we try:
       AI enqueue:       cap.enqueueAI
       Quantum enqueue:  cap.enqueueQuantum
       Get result:       cap.getResult
       List providers:   aicf.listProviders
   - You can override names with --rpc-method-*

2) REST (dev helper / custom):
   - Use --rest-base http://127.0.0.1:8666
   - Endpoints (overridable):
       POST {base}/cap/enqueue/ai        JSON: {"model": "...", "prompt": "..."}
       POST {base}/cap/enqueue/quantum   JSON: {"circuit": {...}, "shots": N}
       GET  {base}/cap/result/{task_id}
       GET  {base}/aicf/providers

Job IDs:
- We try to extract a task id from several common fields: "task_id", "id", "job_id",
  or a dotted path provided by --id-path (e.g. "receipt.task_id").
- For REST result lookups we substitute {task_id} in the URL template.
- For RPC result lookups we call the method with {"task_id": "<...>"} by default
  (override via --rpc-result-param if needed).

Examples
--------
# 10 jobs/sec (70% AI, 30% Quantum) for 60s against JSON-RPC; poll results
python tests/load/soak_aicf_jobs.py \
  --rpc-url http://127.0.0.1:8545 --rps 10 --duration 60 --ai-ratio 0.7 --poll-results 1

# Same but using REST helper endpoints and GET result probability of 50%
python tests/load/soak_aicf_jobs.py \
  --rest-base http://127.0.0.1:8666 --rps 20 --duration 120 --ai-ratio 0.5 --poll-results 1

Dependencies
------------
    pip install httpx

Output
------
Prints a single JSON object to stdout with counters, latency histograms, and percentiles.
Progress is printed to stderr every --progress-every seconds.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


class Hist:
    """Fixed buckets in milliseconds with simple percentile summaries."""

    def __init__(self, bounds_ms: Optional[List[float]] = None) -> None:
        if bounds_ms is None:
            bounds_ms = [
                5,
                10,
                20,
                30,
                50,
                75,
                100,
                150,
                200,
                300,
                400,
                600,
                800,
                1000,
                1500,
                2000,
                3000,
                5000,
                8000,
                12000,
                20000,
            ]
        self.bounds = list(bounds_ms)
        self.counts = [0] * len(self.bounds)
        self.overflow = 0
        self.n = 0

    def observe(self, ms: float) -> None:
        self.n += 1
        for i, ub in enumerate(self.bounds):
            if ms <= ub:
                self.counts[i] += 1
                return
        self.overflow += 1

    def _quantile(self, q: float) -> float:
        if self.n == 0:
            return 0.0
        target = int(max(0, min(self.n - 1, round(q * (self.n - 1)))))
        cum = 0
        for i, c in enumerate(self.counts):
            cum += c
            if cum > target:
                return float(self.bounds[i])
        return float(self.bounds[-1] * 1.5)

    def summary(self) -> Dict[str, float]:
        return {
            "p50_ms": self._quantile(0.50),
            "p90_ms": self._quantile(0.90),
            "p99_ms": self._quantile(0.99),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bounds_ms": self.bounds,
            "counts": self.counts,
            "overflow": self.overflow,
            "n": self.n,
        } | self.summary()


class LCG:
    """Deterministic 64-bit LCG for generating prompts/circuits."""

    def __init__(self, seed: int = 0xA1CFFACE) -> None:
        self.x = (seed & ((1 << 64) - 1)) or 1

    def next_u64(self) -> int:
        self.x = (6364136223846793005 * self.x + 1442695040888963407) & ((1 << 64) - 1)
        return self.x

    def rand_u32(self) -> int:
        return self.next_u64() >> 32

    def rand_float(self) -> float:
        return (self.next_u64() >> 11) * (1.0 / (1 << 53))

    def choice(self, items: List[Any]) -> Any:
        if not items:
            return None
        return items[self.rand_u32() % len(items)]

    def rand_ascii(self, n: int) -> str:
        out = []
        for _ in range(n):
            v = 32 + (self.rand_u32() % 95)  # printable ascii
            out.append(chr(v))
        return "".join(out)


def get_path(d: Any, path: Optional[str]) -> Any:
    """Small dotted-path getter."""
    if d is None or path is None or path == "":
        return None
    cur = d
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def extract_task_id(obj: Any, id_path: Optional[str]) -> Optional[str]:
    cand = None
    if isinstance(obj, dict):
        cand = (
            obj.get("task_id")
            or obj.get("id")
            or obj.get("job_id")
            or obj.get("taskId")
            or obj.get("jobId")
        )
        if cand is None and id_path:
            cand = get_path(obj, id_path)
    return str(cand) if cand is not None else None


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


@dataclass
class RpcConfig:
    url: str
    method_ai: str
    method_quantum: str
    method_result: str
    method_list_providers: str
    result_param_name: str  # usually "task_id"
    request_id_base: int = 1000


class RpcClient:
    def __init__(self, cfg: RpcConfig, http: httpx.AsyncClient) -> None:
        self.cfg = cfg
        self.http = http
        self._rid = cfg.request_id_base

    async def call(
        self, method: str, params: Any
    ) -> Tuple[Optional[Any], Optional[Dict[str, Any]], float]:
        self._rid += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._rid,
            "method": method,
            "params": params,
        }
        t0 = time.perf_counter()
        r = await self.http.post(self.cfg.url, json=payload, timeout=None)
        dt = (time.perf_counter() - t0) * 1000.0
        jd = r.json()
        if "error" in jd and jd["error"] is not None:
            return None, jd.get("error"), dt
        return jd.get("result"), None, dt

    async def enqueue_ai(
        self, model: str, prompt: str
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], float, Any]:
        res, err, dt = await self.call(
            self.cfg.method_ai, {"model": model, "prompt": prompt}
        )
        tid = extract_task_id(res, None)
        if tid is None and isinstance(res, dict):
            tid = extract_task_id(res.get("receipt") or res.get("job") or {}, None)
        return tid, err, dt, res

    async def enqueue_quantum(
        self, circuit: Dict[str, Any], shots: int
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], float, Any]:
        res, err, dt = await self.call(
            self.cfg.method_quantum, {"circuit": circuit, "shots": shots}
        )
        tid = extract_task_id(res, None)
        if tid is None and isinstance(res, dict):
            tid = extract_task_id(res.get("receipt") or res.get("job") or {}, None)
        return tid, err, dt, res

    async def get_result(
        self, task_id: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], float]:
        res, err, dt = await self.call(
            self.cfg.method_result, {self.cfg.result_param_name: task_id}
        )
        return (res if isinstance(res, dict) else None), err, dt

    async def list_providers(
        self,
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]], float]:
        res, err, dt = await self.call(self.cfg.method_list_providers, {})
        if isinstance(res, list):
            return res, err, dt
        return None, err, dt


@dataclass
class RestConfig:
    base: str
    post_ai: str = "/cap/enqueue/ai"
    post_quantum: str = "/cap/enqueue/quantum"
    get_result: str = "/cap/result/{task_id}"
    get_providers: str = "/aicf/providers"
    id_path: Optional[str] = None  # e.g. "receipt.task_id"


class RestClient:
    def __init__(self, cfg: RestConfig, http: httpx.AsyncClient) -> None:
        self.cfg = cfg
        self.http = http

    async def enqueue_ai(
        self, model: str, prompt: str
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], float, Any]:
        t0 = time.perf_counter()
        r = await self.http.post(
            f"{self.cfg.base}{self.cfg.post_ai}",
            json={"model": model, "prompt": prompt},
            timeout=None,
        )
        dt = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return None, {"code": r.status_code, "message": r.text}, dt, None
        jd = r.json()
        tid = extract_task_id(jd, self.cfg.id_path)
        return tid, None, dt, jd

    async def enqueue_quantum(
        self, circuit: Dict[str, Any], shots: int
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], float, Any]:
        t0 = time.perf_counter()
        r = await self.http.post(
            f"{self.cfg.base}{self.cfg.post_quantum}",
            json={"circuit": circuit, "shots": shots},
            timeout=None,
        )
        dt = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return None, {"code": r.status_code, "message": r.text}, dt, None
        jd = r.json()
        tid = extract_task_id(jd, self.cfg.id_path)
        return tid, None, dt, jd

    async def get_result(
        self, task_id: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], float]:
        url = f"{self.cfg.base}{self.cfg.get_result}".replace("{task_id}", task_id)
        t0 = time.perf_counter()
        r = await self.http.get(url, timeout=None)
        dt = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return None, {"code": r.status_code, "message": r.text}, dt
        jd = r.json()
        if isinstance(jd, dict):
            return jd, None, dt
        return None, {"code": 500, "message": "invalid JSON result"}, dt

    async def list_providers(
        self,
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]], float]:
        url = f"{self.cfg.base}{self.cfg.get_providers}"
        t0 = time.perf_counter()
        r = await self.http.get(url, timeout=None)
        dt = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return None, {"code": r.status_code, "message": r.text}, dt
        jd = r.json()
        if isinstance(jd, list):
            return jd, None, dt
        return None, {"code": 500, "message": "invalid providers JSON"}, dt


# ---------------------------------------------------------------------------
# Load state & workload
# ---------------------------------------------------------------------------


@dataclass
class EnqueueRecord:
    task_id: Optional[str]
    kind: str  # "ai" | "quantum"
    t_enqueue_ms: float
    t_submit: float


class SoakState:
    def __init__(self) -> None:
        self.enqueue_hist = Hist()
        self.complete_hist = Hist(
            bounds_ms=[
                50,
                100,
                150,
                200,
                300,
                400,
                600,
                800,
                1000,
                1500,
                2000,
                3000,
                5000,
                8000,
                12000,
                20000,
                30000,
                60000,
            ]
        )
        self.enqueue_ok = 0
        self.enqueue_err = 0
        self.completed_ok = 0
        self.completed_err = 0
        self.emitted = 0
        self.acked = 0
        self.inflight: Dict[str, EnqueueRecord] = {}
        self.last_provider_snapshot: Dict[str, Any] = {}
        self.provider_samples = 0


# ---------------------------------------------------------------------------
# Work generation
# ---------------------------------------------------------------------------


def make_ai_payload(rng: LCG, min_len: int, max_len: int) -> Tuple[str, str]:
    model = rng.choice(["tiny-demo", "echo", "embed-mini", "classify"])
    n = min_len + (rng.rand_u32() % max(1, (max_len - min_len + 1)))
    prompt = f"[seed={rng.next_u64():x}] " + rng.rand_ascii(n)
    return model, prompt


def make_quantum_payload(
    rng: LCG, depth_min: int, depth_max: int, width_min: int, width_max: int
) -> Tuple[Dict[str, Any], int]:
    depth = depth_min + (rng.rand_u32() % max(1, (depth_max - depth_min + 1)))
    width = width_min + (rng.rand_u32() % max(1, (width_max - width_min + 1)))
    shots = 128 + (rng.rand_u32() % 256)
    circuit = {
        "name": "demo_chain_of_h",
        "depth": int(depth),
        "width": int(width),
        "ops": [
            {"op": "h", "q": int(i % max(1, width))}
            for i in range(depth * max(1, width // 4 or 1))
        ],
    }
    return circuit, int(shots)


def result_looks_done(d: Optional[Dict[str, Any]]) -> Optional[bool]:
    if not isinstance(d, dict):
        return None
    st = str(d.get("status") or d.get("state") or "").lower()
    if st in {"ok", "done", "completed", "success", "ready"}:
        return True
    if st in {"failed", "error", "timeout", "expired"}:
        return False
    # Heuristic: presence of "result" or "output" implies success
    if any(k in d for k in ("result", "output")):
        return True
    return None


async def wait_for_result(
    get_result_fn,
    task_id: str,
    state: SoakState,
    start_time: float,
    poll_every_s: float,
    timeout_s: float,
) -> None:
    deadline = start_time + timeout_s
    last_err: Optional[Any] = None
    while True:
        now = time.perf_counter()
        if now >= deadline:
            state.completed_err += 1
            return
        res, err, _ = await get_result_fn(task_id)
        last_err = err or last_err
        verdict = result_looks_done(res)
        if verdict is True:
            dt_ms = (time.perf_counter() - start_time) * 1000.0
            state.complete_hist.observe(dt_ms)
            state.completed_ok += 1
            return
        if verdict is False:
            state.completed_err += 1
            return
        await asyncio.sleep(poll_every_s)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_soak(
    *,
    rpc: Optional[RpcClient],
    rest: Optional[RestClient],
    use_backend: str,
    rps: float,
    duration_s: float,
    max_inflight: int,
    poll_results: bool,
    poll_every_s: float,
    result_timeout_s: float,
    ai_ratio: float,
    seed: int,
    ai_len_min: int,
    ai_len_max: int,
    q_depth_min: int,
    q_depth_max: int,
    q_width_min: int,
    q_width_max: int,
    progress_every_s: float,
    providers_every_s: float,
) -> Dict[str, Any]:
    assert use_backend in {"rpc", "rest"}
    rng = LCG(seed=seed)
    state = SoakState()
    limits = httpx.Limits(max_keepalive_connections=512, max_connections=1024)
    async with httpx.AsyncClient(limits=limits, timeout=None) as http:
        # rebind clients with shared http session if needed
        if rpc is not None:
            rpc.http = http
        if rest is not None:
            rest.http = http

        inflight_tasks: set[asyncio.Task] = set()

        async def do_enqueue(kind: str) -> None:
            nonlocal state
            try:
                if kind == "ai":
                    model, prompt = make_ai_payload(rng, ai_len_min, ai_len_max)
                    if use_backend == "rpc":
                        tid, err, dt, _ = await rpc.enqueue_ai(model, prompt)  # type: ignore
                    else:
                        tid, err, dt, _ = await rest.enqueue_ai(model, prompt)  # type: ignore
                else:
                    circuit, shots = make_quantum_payload(
                        rng, q_depth_min, q_depth_max, q_width_min, q_width_max
                    )
                    if use_backend == "rpc":
                        tid, err, dt, _ = await rpc.enqueue_quantum(circuit, shots)  # type: ignore
                    else:
                        tid, err, dt, _ = await rest.enqueue_quantum(circuit, shots)  # type: ignore

                state.enqueue_hist.observe(dt)
                state.acked += 1
                if err is None and tid:
                    state.enqueue_ok += 1
                    if poll_results:
                        rec = EnqueueRecord(
                            task_id=tid,
                            kind=kind,
                            t_enqueue_ms=dt,
                            t_submit=time.perf_counter(),
                        )
                        state.inflight[tid] = rec

                        async def waiter(task_id: str) -> None:
                            get_fn = rpc.get_result if use_backend == "rpc" else rest.get_result  # type: ignore
                            await wait_for_result(
                                get_fn,
                                task_id,
                                state,
                                rec.t_submit,
                                poll_every_s,
                                result_timeout_s,
                            )
                            state.inflight.pop(task_id, None)

                        awaiter = asyncio.create_task(waiter(tid))
                        inflight_tasks.add(awaiter)
                        awaiter.add_done_callback(inflight_tasks.discard)
                else:
                    state.enqueue_err += 1
            except Exception:
                state.enqueue_err += 1

        # Schedule
        t0 = time.perf_counter()
        deadline = t0 + duration_s
        next_emit = t0
        dt = 1.0 / max(1e-6, rps)
        last_progress = t0
        last_providers = t0

        while True:
            now = time.perf_counter()

            # Emit work
            while (
                next_emit <= now
                and now < deadline
                and (len(inflight_tasks) < max_inflight)
            ):
                state.emitted += 1
                kind = "ai" if (rng.rand_float() < ai_ratio) else "quantum"
                task = asyncio.create_task(do_enqueue(kind))
                inflight_tasks.add(task)
                task.add_done_callback(inflight_tasks.discard)
                next_emit += dt

            # Provider snapshot (best-effort)
            if (now - last_providers) >= providers_every_s:
                try:
                    if use_backend == "rpc" and rpc is not None:
                        provs, _, _ = await rpc.list_providers()
                    elif rest is not None:
                        provs, _, _ = await rest.list_providers()
                    else:
                        provs = None
                    if isinstance(provs, list):
                        active = [
                            p
                            for p in provs
                            if str(p.get("status", "")).lower()
                            in {"active", "online", "ready"}
                        ]
                        state.last_provider_snapshot = {
                            "total": len(provs),
                            "active": len(active),
                        }
                        state.provider_samples += 1
                except Exception:
                    pass
                last_providers = now

            # Progress
            if (now - last_progress) >= progress_every_s:
                enq = state.enqueue_hist.summary()
                done = state.complete_hist.summary()
                print(
                    f"[{now - t0:6.1f}s] emit={state.emitted} ack={state.acked} enq_ok/err={state.enqueue_ok}/{state.enqueue_err} "
                    f"POST p50={enq['p50_ms']:4.0f}ms p90={enq['p90_ms']:4.0f}ms p99={enq['p99_ms']:4.0f}ms "
                    f"done_ok/err={state.completed_ok}/{state.completed_err} "
                    f"TTComplete p50={done['p50_ms']:4.0f}ms p90={done['p90_ms']:4.0f}ms p99={done['p99_ms']:4.0f}ms "
                    f"inflight_waiters={len(inflight_tasks)} providers={state.last_provider_snapshot or '-'}",
                    file=sys.stderr,
                    flush=True,
                )
                last_progress = now

            if now >= deadline and not inflight_tasks:
                break

            await asyncio.sleep(min(0.005, max(0.0, next_emit - now)))

        # Drain just in case
        if inflight_tasks:
            await asyncio.gather(*inflight_tasks, return_exceptions=True)

        elapsed = max(1e-9, time.perf_counter() - t0)
        out = {
            "case": "load.soak_aicf_jobs",
            "params": {
                "backend": use_backend,
                "rps": rps,
                "duration_s": duration_s,
                "max_inflight": max_inflight,
                "poll_results": poll_results,
                "poll_every_s": poll_every_s,
                "result_timeout_s": result_timeout_s,
                "ai_ratio": ai_ratio,
                "seed": seed,
                "ai_len": [ai_len_min, ai_len_max],
                "quantum_depth": [q_depth_min, q_depth_max],
                "quantum_width": [q_width_min, q_width_max],
            },
            "result": {
                "elapsed_s": elapsed,
                "emitted": state.emitted,
                "acked": state.acked,
                "enqueue_ok": state.enqueue_ok,
                "enqueue_err": state.enqueue_err,
                "completed_ok": state.completed_ok,
                "completed_err": state.completed_err,
                "enqueue_latency": state.enqueue_hist.to_dict(),
                "complete_latency": state.complete_hist.to_dict(),
                "providers_last": state.last_provider_snapshot,
                "providers_samples": state.provider_samples,
            },
        }
        return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Enqueue AI/Quantum jobs against AICF; monitor completion and basic SLAs."
    )
    g_backend = ap.add_mutually_exclusive_group(required=True)
    g_backend.add_argument(
        "--rpc-url", help="JSON-RPC endpoint (enqueues via RPC methods)"
    )
    g_backend.add_argument("--rest-base", help="REST base URL for helper endpoints")

    # RPC method names
    ap.add_argument(
        "--rpc-method-ai",
        default="cap.enqueueAI",
        help="RPC method for AI enqueue (default: cap.enqueueAI)",
    )
    ap.add_argument(
        "--rpc-method-quantum",
        default="cap.enqueueQuantum",
        help="RPC method for Quantum enqueue (default: cap.enqueueQuantum)",
    )
    ap.add_argument(
        "--rpc-method-result",
        default="cap.getResult",
        help="RPC method to fetch a result (default: cap.getResult)",
    )
    ap.add_argument(
        "--rpc-method-list-providers",
        default="aicf.listProviders",
        help="RPC method to list providers (default: aicf.listProviders)",
    )
    ap.add_argument(
        "--rpc-result-param",
        default="task_id",
        help="Param name for result lookup (default: task_id)",
    )

    # REST paths
    ap.add_argument(
        "--rest-post-ai",
        default="/cap/enqueue/ai",
        help="REST path for AI enqueue (default: /cap/enqueue/ai)",
    )
    ap.add_argument(
        "--rest-post-quantum",
        default="/cap/enqueue/quantum",
        help="REST path for Quantum enqueue (default: /cap/enqueue/quantum)",
    )
    ap.add_argument(
        "--rest-get-result",
        default="/cap/result/{task_id}",
        help="REST path for result lookup (default: /cap/result/{task_id})",
    )
    ap.add_argument(
        "--rest-get-providers",
        default="/aicf/providers",
        help="REST path to list providers (default: /aicf/providers)",
    )
    ap.add_argument(
        "--id-path",
        default=None,
        help="Dotted path to task id in REST enqueue response (fallback if no common field found)",
    )

    # Load shape
    ap.add_argument(
        "--rps",
        type=float,
        default=10.0,
        help="Target enqueues per second (default: 10)",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Test duration seconds (default: 60)",
    )
    ap.add_argument(
        "--max-inflight",
        type=int,
        default=512,
        help="Max concurrent waiter tasks (default: 512)",
    )

    ap.add_argument(
        "--poll-results",
        type=int,
        default=1,
        help="Whether to poll for results (1/0, default: 1)",
    )
    ap.add_argument(
        "--poll-every",
        type=float,
        default=1.0,
        help="Polling cadence seconds (default: 1.0)",
    )
    ap.add_argument(
        "--result-timeout",
        type=float,
        default=90.0,
        help="Timeout seconds per job result (default: 90)",
    )

    # Mix & payload knobs
    ap.add_argument(
        "--ai-ratio",
        type=float,
        default=0.7,
        help="Fraction of AI jobs (0..1); remainder Quantum (default: 0.7)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=20240913,
        help="Deterministic seed for prompts/circuits (default: 20240913)",
    )
    ap.add_argument(
        "--ai-len-min",
        type=int,
        default=64,
        help="Min prompt length (chars) (default: 64)",
    )
    ap.add_argument(
        "--ai-len-max",
        type=int,
        default=256,
        help="Max prompt length (chars) (default: 256)",
    )
    ap.add_argument(
        "--q-depth-min",
        type=int,
        default=8,
        help="Quantum circuit min depth (default: 8)",
    )
    ap.add_argument(
        "--q-depth-max",
        type=int,
        default=32,
        help="Quantum circuit max depth (default: 32)",
    )
    ap.add_argument(
        "--q-width-min",
        type=int,
        default=4,
        help="Quantum circuit min width (default: 4)",
    )
    ap.add_argument(
        "--q-width-max",
        type=int,
        default=12,
        help="Quantum circuit max width (default: 12)",
    )

    # Telemetry
    ap.add_argument(
        "--progress-every",
        type=float,
        default=5.0,
        help="Progress print cadence seconds (default: 5)",
    )
    ap.add_argument(
        "--providers-every",
        type=float,
        default=15.0,
        help="Provider snapshot cadence seconds (default: 15)",
    )

    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if args.rps <= 0 or args.duration <= 0:
        print("rps and duration must be > 0", file=sys.stderr)
        return 2
    if not (0.0 <= args.ai_ratio <= 1.0):
        print("--ai-ratio must be between 0 and 1", file=sys.stderr)
        return 2
    if args.poll_results not in (0, 1):
        print("--poll-results must be 0 or 1", file=sys.stderr)
        return 2

    rpc_client: Optional[RpcClient] = None
    rest_client: Optional[RestClient] = None
    dummy_http = (
        httpx.AsyncClient()
    )  # replaced in run_soak with a tuned client; we close it immediately
    try:
        if args.rpc_url:
            rpc_client = RpcClient(
                RpcConfig(
                    url=args.rpc_url,
                    method_ai=args.rpc_method_ai,
                    method_quantum=args.rpc_method_quantum,
                    method_result=args.rpc_method_result,
                    method_list_providers=args.rpc_method_list_providers,
                    result_param_name=args.rpc_result_param,
                ),
                dummy_http,
            )
            backend = "rpc"
        else:
            rest_client = RestClient(
                RestConfig(
                    base=args.rest_base,
                    post_ai=args.rest_post_ai,
                    post_quantum=args.rest_post_quantum,
                    get_result=args.rest_get_result,
                    get_providers=args.rest_get_providers,
                    id_path=args.id_path,
                ),
                dummy_http,
            )
            backend = "rest"
    finally:
        try:
            dummy_http.aclose()
        except Exception:
            pass

    res = asyncio.run(
        run_soak(
            rpc=rpc_client,
            rest=rest_client,
            use_backend=backend,
            rps=float(args.rps),
            duration_s=float(args.duration),
            max_inflight=int(args.max_inflight),
            poll_results=bool(args.poll_results),
            poll_every_s=float(args.poll_every),
            result_timeout_s=float(args.result_timeout),
            ai_ratio=float(args.ai_ratio),
            seed=int(args.seed),
            ai_len_min=int(args.ai_len_min),
            ai_len_max=int(args.ai_len_max),
            q_depth_min=int(args.q_depth_min),
            q_depth_max=int(args.q_depth_max),
            q_width_min=int(args.q_width_min),
            q_width_max=int(args.q_width_max),
            progress_every_s=float(args.progress_every),
            providers_every_s=float(args.providers_every),
        )
    )
    print(json.dumps(res, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
