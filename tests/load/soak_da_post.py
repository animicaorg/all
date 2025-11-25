# -*- coding: utf-8 -*-
"""
soak_da_post.py
================

Steady **Data Availability** blob POST load with optional **availability
sampling** and blob GET verification. Emits latency histograms and throughput
stats as JSON.

Assumptions (aligned with da/retrieval/api.py in this repo):
- POST `${BASE}/da/blob?ns=<int>` with Content-Type: application/octet-stream
  returns JSON like: {"commitment":"0x…","namespace":<int>,"size":<int>}
- GET  `${BASE}/da/blob/{commitment}` returns raw bytes
- GET  `${BASE}/da/proof?commitment=0x…&samples=N` (or `/da/proof/{commitment}?samples=N`)
  returns a proof object (schema-agnostic here; we only check HTTP 200)

If your deployment uses slightly different routes, pass `--post-path`, `--get-path`,
`--proof-path` to override.

Requirements:
    pip install httpx

Examples:
    # 20 blobs/sec for 2 minutes, size 64–512 KiB uniformly, do proofs of 64 samples
    python tests/load/soak_da_post.py --base http://127.0.0.1:8666 \\
        --rps 20 --duration 120 --min-kib 64 --max-kib 512 --samples 64

    # Also GET the blob back for 10% of posts
    python tests/load/soak_da_post.py --get-prob 0.1

Notes:
- Uses a lightweight PRNG (LCG) for deterministic pseudo-random payloads.
- Reports progress to stderr every few seconds; prints a single JSON object to stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx


# -----------------------------------------------------------------------------
# Small utilities
# -----------------------------------------------------------------------------

class Hist:
    """Fixed buckets in milliseconds; upper-bound semantics."""
    def __init__(self, buckets_ms: Optional[List[float]] = None) -> None:
        if buckets_ms is None:
            buckets_ms = [5,10,20,30,50,75,100,150,200,300,400,600,800,1000,1500,2000,3000,5000,8000,12000,20000]
        self.bounds = list(buckets_ms)
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
        return {"p50_ms": self._quantile(0.50), "p90_ms": self._quantile(0.90), "p99_ms": self._quantile(0.99)}

    def to_dict(self) -> Dict:
        return {"bounds_ms": self.bounds, "counts": self.counts, "overflow": self.overflow, "n": self.n} | self.summary()


class LCG:
    """Deterministic 64-bit LCG for sizes + payload bytes."""
    def __init__(self, seed: int = 0xC0FFEE) -> None:
        self.x = (seed & ((1 << 64) - 1)) or 1

    def next_u64(self) -> int:
        self.x = (6364136223846793005 * self.x + 1442695040888963407) & ((1 << 64) - 1)
        return self.x

    def rand_u32(self) -> int:
        return self.next_u64() >> 32

    def rand_float(self) -> float:
        return (self.next_u64() >> 11) * (1.0 / (1 << 53))  # ~[0,1)


# -----------------------------------------------------------------------------
# Config & State
# -----------------------------------------------------------------------------

@dataclass
class Paths:
    post: str
    get: str
    proof: str


class SoakState:
    __slots__ = (
        "post_hist", "get_hist", "proof_hist",
        "posted_ok", "posted_err",
        "get_ok", "get_err",
        "proof_ok", "proof_err",
        "emitted", "completed",
        "bytes_sent",
    )
    def __init__(self) -> None:
        self.post_hist = Hist()
        self.get_hist = Hist()
        self.proof_hist = Hist(buckets_ms=[10,20,30,50,75,100,150,200,300,400,600,800,1000,1500,2000,3000,5000])
        self.posted_ok = 0
        self.posted_err = 0
        self.get_ok = 0
        self.get_err = 0
        self.proof_ok = 0
        self.proof_err = 0
        self.emitted = 0
        self.completed = 0
        self.bytes_sent = 0


# -----------------------------------------------------------------------------
# Work item
# -----------------------------------------------------------------------------

async def _try_get(client: httpx.AsyncClient, base: str, p: Paths, commitment: str, state: SoakState) -> None:
    t0 = time.perf_counter()
    # Prefer /da/blob/{commitment}
    url = f"{base}{p.get}".replace("{commitment}", commitment)
    try:
        r = await client.get(url, timeout=None)
        dt = (time.perf_counter() - t0) * 1000.0
        state.get_hist.observe(dt)
        if r.status_code == 200:
            state.get_ok += 1
        else:
            state.get_err += 1
    except Exception:
        state.get_err += 1


async def _try_proof(client: httpx.AsyncClient, base: str, p: Paths, commitment: str, samples: int, state: SoakState) -> None:
    t0 = time.perf_counter()
    # Try query style first
    url_q = f"{base}{p.proof}"
    if "{commitment}" in url_q:
        url = url_q.replace("{commitment}", commitment)
        sep = "&" if ("?" in url) else "?"
        url = f"{url}{sep}samples={samples}"
    else:
        sep = "&" if ("?" in url_q) else "?"
        url = f"{url_q}{sep}commitment={commitment}&samples={samples}"
    try:
        r = await client.get(url, timeout=None)
        dt = (time.perf_counter() - t0) * 1000.0
        state.proof_hist.observe(dt)
        if r.status_code == 200:
            # We don't validate proof structure here (load path); HTTP 200 is success.
            state.proof_ok += 1
        else:
            state.proof_err += 1
    except Exception:
        state.proof_err += 1


async def do_post_one(
    client: httpx.AsyncClient,
    base: str,
    p: Paths,
    ns: int,
    blob: bytes,
    state: SoakState,
    do_get_prob: float,
    do_proof_samples: int,
    rpc_id: int,
) -> None:
    # POST /da/blob?ns=<int>
    t0 = time.perf_counter()
    url = f"{base}{p.post}"
    if "?" in url:
        url = f"{url}&ns={ns}"
    else:
        url = f"{url}?ns={ns}"
    try:
        r = await client.post(url, content=blob, headers={"Content-Type": "application/octet-stream"}, timeout=None)
        dt = (time.perf_counter() - t0) * 1000.0
        state.post_hist.observe(dt)
        state.completed += 1
        if r.status_code == 200:
            state.posted_ok += 1
            state.bytes_sent += len(blob)
            jd = {}
            try:
                jd = r.json()
            except Exception:
                pass
            commitment = None
            if isinstance(jd, dict):
                c = jd.get("commitment") or jd.get("root") or jd.get("commit")
                if isinstance(c, str):
                    commitment = c
            # Best-effort GET / PROOF followups
            if commitment:
                # Optional GET
                if do_get_prob > 0.0:
                    # Bernoulli draw using LCG from id
                    if (hash((rpc_id * 1103515245 + 12345)) & 0xFFFF) / 65535.0 < do_get_prob:
                        await _try_get(client, base, p, commitment, state)
                # Optional PROOF
                if do_proof_samples > 0:
                    await _try_proof(client, base, p, commitment, do_proof_samples, state)
        else:
            state.posted_err += 1
    except Exception:
        state.posted_err += 1


# -----------------------------------------------------------------------------
# Runner (cadence control)
# -----------------------------------------------------------------------------

async def run_soak(
    base: str,
    paths: Paths,
    rps: float,
    duration_s: float,
    max_inflight: int,
    min_kib: int,
    max_kib: int,
    ns_min: int,
    ns_max: int,
    seed: int,
    get_prob: float,
    samples: int,
    progress_every_s: float,
) -> Dict:
    lcg = LCG(seed=seed)
    # Connections
    limits = httpx.Limits(max_keepalive_connections=512, max_connections=1024)
    async with httpx.AsyncClient(limits=limits, timeout=None) as client:
        state = SoakState()
        inflight: set[asyncio.Task] = set()
        t0 = time.perf_counter()
        deadline = t0 + float(duration_s)
        next_tick = t0
        dt = 1.0 / max(1e-6, rps)
        last_report = t0
        rpc_id = 0

        while True:
            now = time.perf_counter()
            # Emit as cadence allows
            while next_tick <= now and (len(inflight) < max_inflight) and (now < deadline):
                # Choose size (uniform between min/max KiB)
                if min_kib > max_kib:
                    min_kib, max_kib = max_kib, min_kib
                kib = min_kib + (lcg.rand_u32() % max(1, (max_kib - min_kib + 1)))
                nbytes = kib * 1024
                # Fill buffer deterministically
                buf = bytearray(nbytes)
                x = lcg.next_u64()
                for i in range(nbytes):
                    x = (6364136223846793005 * x + 1442695040888963407) & ((1 << 64) - 1)
                    buf[i] = (x >> 56) & 0xFF
                # Namespace range
                ns_range = max(1, ns_max - ns_min + 1)
                ns = ns_min + (lcg.rand_u32() % ns_range)

                state.emitted += 1
                rpc_id += 1
                task = asyncio.create_task(
                    do_post_one(
                        client, base, paths, ns, bytes(buf), state,
                        do_get_prob=get_prob,
                        do_proof_samples=samples,
                        rpc_id=rpc_id,
                    )
                )
                inflight.add(task)
                task.add_done_callback(inflight.discard)
                next_tick += dt

            # Progress
            if (now - last_report) >= progress_every_s:
                elapsed = now - t0
                acks = state.completed
                ok = state.posted_ok
                err = state.posted_err
                rate = acks / max(1e-9, elapsed)
                p = state.post_hist.summary()
                print(
                    f"[{elapsed:6.1f}s] emitted={state.emitted} acked={acks} ok={ok} err={err} "
                    f"bytes={state.bytes_sent/1e6:7.2f}MB rate≈{rate:6.1f} rps "
                    f"POST p50={p['p50_ms']:4.0f}ms p90={p['p90_ms']:4.0f}ms p99={p['p99_ms']:4.0f}ms "
                    f"GET ok/err={state.get_ok}/{state.get_err} PROOF ok/err={state.proof_ok}/{state.proof_err}",
                    file=sys.stderr,
                    flush=True,
                )
                last_report = now

            if now >= deadline and not inflight:
                break

            await asyncio.sleep(min(0.005, max(0.0, next_tick - now)))

        # Drain (should be empty)
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)

        elapsed = max(1e-9, time.perf_counter() - t0)
        out = {
            "case": "load.soak_da_post",
            "params": {
                "base": base, "paths": paths.__dict__, "rps": rps, "duration_s": duration_s,
                "max_inflight": max_inflight, "min_kib": min_kib, "max_kib": max_kib,
                "ns_min": ns_min, "ns_max": ns_max, "seed": seed,
                "get_prob": get_prob, "samples": samples
            },
            "result": {
                "elapsed_s": elapsed,
                "emitted": state.emitted,
                "acked": state.completed,
                "posted_ok": state.posted_ok,
                "posted_err": state.posted_err,
                "bytes_sent": state.bytes_sent,
                "mbps_avg": (state.bytes_sent * 8.0 / 1e6) / elapsed,
                "post_latency": state.post_hist.to_dict(),
                "get": {
                    "ok": state.get_ok, "err": state.get_err,
                    "latency": state.get_hist.to_dict()
                },
                "proof": {
                    "ok": state.proof_ok, "err": state.proof_err,
                    "latency": state.proof_hist.to_dict()
                },
            },
        }
        return out


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Steady DA POST load with optional availability sampling and GET verification.")
    ap.add_argument("--base", default=os.environ.get("DA_URL", "http://127.0.0.1:8666"),
                    help="Base URL for DA service (default: %(default)s or DA_URL env)")
    ap.add_argument("--post-path", default="/da/blob", help="POST path (default: /da/blob)")
    ap.add_argument("--get-path", default="/da/blob/{commitment}", help="GET path template (default: /da/blob/{commitment})")
    ap.add_argument("--proof-path", default="/da/proof", help="Proof path (supports {commitment} or ?commitment=)")

    ap.add_argument("--rps", type=float, default=10.0, help="Target POSTs per second (default: 10)")
    ap.add_argument("--duration", type=float, default=60.0, help="Duration seconds (default: 60)")
    ap.add_argument("--max-inflight", type=int, default=200, help="Max concurrent in-flight requests (default: 200)")

    ap.add_argument("--min-kib", type=int, default=64, help="Minimum blob size KiB (default: 64)")
    ap.add_argument("--max-kib", type=int, default=512, help="Maximum blob size KiB (default: 512)")

    ap.add_argument("--ns-min", type=int, default=16, help="Minimum namespace id (default: 16)")
    ap.add_argument("--ns-max", type=int, default=24, help="Maximum namespace id (default: 24)")

    ap.add_argument("--seed", type=int, default=424242, help="Deterministic payload seed (default: 424242)")

    ap.add_argument("--get-prob", type=float, default=0.0,
                    help="Probability (0..1) to GET the blob after POST (default: 0.0)")
    ap.add_argument("--samples", type=int, default=0,
                    help="If >0, request a DAS proof with this many samples after POST (default: 0)")

    ap.add_argument("--progress-every", type=float, default=5.0, help="Progress print cadence seconds (default: 5)")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.rps <= 0 or args.duration <= 0:
        print("rps and duration must be > 0", file=sys.stderr)
        return 2
    if not (0.0 <= args.get_prob <= 1.0):
        print("--get-prob must be between 0 and 1", file=sys.stderr)
        return 2
    if args.samples < 0:
        print("--samples must be >= 0", file=sys.stderr)
        return 2

    paths = Paths(post=args.post_path, get=args.get_path, proof=args.proof_path)
    res = asyncio.run(run_soak(
        base=args.base,
        paths=paths,
        rps=float(args.rps),
        duration_s=float(args.duration),
        max_inflight=int(args.max_inflight),
        min_kib=int(args.min_kib),
        max_kib=int(args.max_kib),
        ns_min=int(args.ns_min),
        ns_max=int(args.ns_max),
        seed=int(args.seed),
        get_prob=float(args.get_prob),
        samples=int(args.samples),
        progress_every_s=float(args.progress_every),
    ))
    print(json.dumps(res, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
