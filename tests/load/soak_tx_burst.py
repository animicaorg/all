# -*- coding: utf-8 -*-
"""
soak_tx_burst.py
================

Sustained TX submit rate generator with **latency histograms**.

This script drives a JSON-RPC node with `tx.sendRawTransaction` calls at a
target requests-per-second (RPS), measures **ack latency** (client → RPC
response), and optionally polls for **receipt latency**.

It is designed to work even without valid signatures (useful for pipeline soak);
success/error rates are reported separately. If you provide a directory with
CBOR files, their bytes will be used as payloads in a round-robin loop.

Requirements:
    pip install httpx

Examples:
    # 100 rps for 60s against default localhost:
    python tests/load/soak_tx_burst.py --rps 100 --duration 60

    # Use CBOR fixtures (cycled) and higher concurrency:
    python tests/load/soak_tx_burst.py --rps 200 --duration 120 \\
        --payload-dir execution/fixtures --max-inflight 500

    # Track receipts (best effort; requires valid txs on a mining devnet):
    python tests/load/soak_tx_burst.py --rps 50 --duration 90 --track-receipts
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import json
import asyncio
import pathlib
from typing import Optional, List, Tuple, Dict

import httpx


# -----------------------------------------------------------------------------
# Payload source (round-robin over CBOR files or synthetic bytes)
# -----------------------------------------------------------------------------

def _load_payloads_from_dir(dirpath: str) -> List[bytes]:
    p = pathlib.Path(dirpath)
    if not p.is_dir():
        raise FileNotFoundError(f"payload dir not found: {dirpath}")
    files = sorted([fp for fp in p.glob("**/*.cbor") if fp.is_file()])
    out: List[bytes] = []
    for fp in files:
        try:
            out.append(fp.read_bytes())
        except Exception:
            # Skip unreadable files
            continue
    return out


class PayloadGen:
    """
    Deterministic payload generator.
    If 'payloads' provided, cycles through them. Otherwise emits synthetic bytes
    that *look* like CBOR-ish blobs (but will likely fail signature checks).
    """
    __slots__ = ("_payloads", "_i", "_seed")

    def __init__(self, payloads: Optional[List[bytes]] = None, seed: int = 1337) -> None:
        self._payloads = payloads or []
        self._i = 0
        self._seed = (seed & ((1 << 64) - 1)) or 1

    @staticmethod
    def _lcg_next(x: int) -> int:
        # 64-bit LCG
        a = 6364136223846793005
        c = 1442695040888963407
        return (a * x + c) & ((1 << 64) - 1)

    def next_bytes(self) -> bytes:
        if self._payloads:
            b = self._payloads[self._i]
            self._i += 1
            if self._i == len(self._payloads):
                self._i = 0
            return b
        # Synthetic: variable-length pseudo-random bytes (128..512 B)
        self._seed = self._lcg_next(self._seed)
        length = 128 + ((self._seed >> 32) & 0xFF) * 2  # 128..~640
        buf = bytearray(length)
        x = self._seed
        for i in range(length):
            x = self._lcg_next(x)
            buf[i] = (x >> 56) & 0xFF
        # Prefix with a plausible CBOR major type header (map-ish), purely cosmetic
        if length >= 3:
            buf[0] = 0xA2  # map(2)
            buf[1] = 0x01  # key 1
            buf[2] = 0x58  # bytes (one-byte length)
        return bytes(buf)


# -----------------------------------------------------------------------------
# Histogram utilities (fixed buckets, approximate quantiles)
# -----------------------------------------------------------------------------

class Hist:
    """
    Fixed bucket histogram in milliseconds. Buckets are *upper bounds*.
    """
    def __init__(self, buckets_ms: Optional[List[float]] = None) -> None:
        if buckets_ms is None:
            # Tuned for RPC latencies: 5ms .. 10s
            buckets_ms = [5,10,20,30,50,75,100,150,200,300,400,600,800,1000,1500,2000,3000,5000,8000,10000]
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

    def to_dict(self) -> Dict:
        return {
            "bounds_ms": self.bounds,
            "counts": self.counts,
            "overflow": self.overflow,
            "n": self.n,
        }

    def _quantile(self, q: float) -> float:
        if self.n == 0:
            return 0.0
        target = int(max(0, min(self.n - 1, round(q * (self.n - 1)))))
        cum = 0
        for i, c in enumerate(self.counts):
            cum += c
            if cum > target:
                return float(self.bounds[i])
        # overflow → return a rough bound (last bucket * 1.5)
        return float(self.bounds[-1] * 1.5)

    def summary(self) -> Dict[str, float]:
        return {"p50_ms": self._quantile(0.50), "p90_ms": self._quantile(0.90), "p99_ms": self._quantile(0.99)}


# -----------------------------------------------------------------------------
# Submit + (optional) receipt tracking
# -----------------------------------------------------------------------------

class SoakState:
    __slots__ = (
        "ack_hist", "rcpt_hist",
        "ok", "err",
        "receipt_ok", "receipt_timeout",
        "submitted", "completed",
    )
    def __init__(self) -> None:
        self.ack_hist = Hist()
        self.rcpt_hist = Hist(buckets_ms=[50,100,200,300,500,750,1000,1500,2000,3000,5000,8000,12000,20000,30000])
        self.ok = 0
        self.err = 0
        self.receipt_ok = 0
        self.receipt_timeout = 0
        self.submitted = 0
        self.completed = 0


async def submit_one(
    client: httpx.AsyncClient,
    url: str,
    payload_hex: str,
    state: SoakState,
    track_receipts: bool,
    receipt_timeout_s: float,
    receipt_poll_every_s: float,
    rpc_id: int,
) -> None:
    t0 = time.perf_counter()
    req = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tx.sendRawTransaction",
        "params": [payload_hex],
    }
    try:
        r = await client.post(url, json=req, timeout=None)
        t1 = time.perf_counter()
        ack_ms = (t1 - t0) * 1000.0
        state.ack_hist.observe(ack_ms)
        state.completed += 1
        data = r.json()
        if r.status_code == 200 and "result" in data and isinstance(data["result"], str):
            state.ok += 1
            if track_receipts:
                tx_hash = data["result"]
                # Poll for receipt
                rcpt_deadline = time.perf_counter() + float(max(0.1, receipt_timeout_s))
                while True:
                    q = {
                        "jsonrpc": "2.0",
                        "id": f"rcpt-{rpc_id}",
                        "method": "tx.getTransactionReceipt",
                        "params": [tx_hash],
                    }
                    rr = await client.post(url, json=q, timeout=None)
                    if rr.status_code == 200:
                        jd = rr.json()
                        if "result" in jd and jd["result"]:
                            done_ms = (time.perf_counter() - t0) * 1000.0
                            state.rcpt_hist.observe(done_ms)
                            state.receipt_ok += 1
                            break
                    if time.perf_counter() >= rcpt_deadline:
                        state.receipt_timeout += 1
                        break
                    await asyncio.sleep(receipt_poll_every_s)
        else:
            state.err += 1
    except Exception:
        state.err += 1


# -----------------------------------------------------------------------------
# Rate control (token/cadence) and runner
# -----------------------------------------------------------------------------

async def run_soak(
    url: str,
    rps: float,
    duration_s: float,
    max_inflight: int,
    track_receipts: bool,
    receipt_timeout_s: float,
    receipt_poll_every_s: float,
    payload_dir: Optional[str],
    seed: int,
    progress_every_s: float,
) -> Dict:
    payloads = _load_payloads_from_dir(payload_dir) if payload_dir else []
    gen = PayloadGen(payloads=payloads, seed=seed)

    limits = httpx.Limits(max_keepalive_connections=512, max_connections=1024)
    async with httpx.AsyncClient(limits=limits, timeout=None) as client:
        state = SoakState()
        inflight: set[asyncio.Task] = set()
        t0 = time.perf_counter()
        deadline = t0 + float(duration_s)
        next_tick = t0
        dt = 1.0 / max(1e-6, rps)
        rpc_id = 0
        last_report = t0

        while True:
            now = time.perf_counter()
            # Launch as many sends as we should have emitted by now (cadence)
            while next_tick <= now and (len(inflight) < max_inflight) and (now < deadline):
                b = gen.next_bytes()
                payload_hex = "0x" + b.hex()
                state.submitted += 1
                rpc_id += 1
                task = asyncio.create_task(submit_one(
                    client, url, payload_hex, state,
                    track_receipts, receipt_timeout_s, receipt_poll_every_s, rpc_id
                ))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
                next_tick += dt

            # Progress report
            if (now - last_report) >= progress_every_s:
                elapsed = now - t0
                sends = state.submitted
                acks = state.completed
                ok = state.ok
                err = state.err
                curr_rps = acks / max(1e-9, elapsed)
                p = state.ack_hist.summary()
                print(
                    f"[{elapsed:6.1f}s] emitted={sends} acked={acks} ok={ok} err={err} "
                    f"rate≈{curr_rps:7.1f} rps p50={p['p50_ms']:5.0f}ms p90={p['p90_ms']:5.0f}ms p99={p['p99_ms']:5.0f}ms",
                    file=sys.stderr,
                    flush=True,
                )
                last_report = now

            if now >= deadline and not inflight:
                break

            # Yield control briefly
            await asyncio.sleep(min(0.005, max(0.0, next_tick - now)))

        # Drain any remaining tasks (should be none)
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)

        elapsed = max(1e-9, time.perf_counter() - t0)
        result = {
            "case": "load.soak_tx_burst",
            "params": {
                "url": url,
                "rps": rps,
                "duration_s": duration_s,
                "max_inflight": max_inflight,
                "track_receipts": track_receipts,
                "receipt_timeout_s": receipt_timeout_s,
                "receipt_poll_every_s": receipt_poll_every_s,
                "payload_dir": payload_dir or "",
                "seed": seed,
            },
            "result": {
                "elapsed_s": elapsed,
                "emitted": state.submitted,
                "acked": state.completed,
                "ok": state.ok,
                "err": state.err,
                "ack_rate_rps": state.completed / elapsed,
                "ack_latency_hist": state.ack_hist.to_dict() | state.ack_hist.summary(),
                "receipt": {
                    "tracked_ok": state.receipt_ok,
                    "tracked_timeout": state.receipt_timeout,
                    "latency_hist": state.rcpt_hist.to_dict() | state.rcpt_hist.summary(),
                } if track_receipts else None,
            },
        }
        return result


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Sustain tx.sendRawTransaction at a fixed RPS and record latency histograms.")
    ap.add_argument("--url", default=os.environ.get("RPC_URL", "http://127.0.0.1:8545/rpc"),
                    help="JSON-RPC endpoint (default: %(default)s or RPC_URL env)")
    ap.add_argument("--rps", type=float, default=100.0, help="Target requests per second (default: 100)")
    ap.add_argument("--duration", type=float, default=60.0, help="Duration in seconds (default: 60)")
    ap.add_argument("--max-inflight", type=int, default=400, help="Max in-flight requests (default: 400)")
    ap.add_argument("--payload-dir", type=str, default=None, help="Directory of *.cbor payload files to cycle")
    ap.add_argument("--seed", type=int, default=1337, help="Deterministic seed for synthetic payloads (default: 1337)")

    ap.add_argument("--track-receipts", action="store_true", help="Poll tx.getTransactionReceipt for success latency")
    ap.add_argument("--receipt-timeout", type=float, default=20.0, help="Per-tx receipt timeout seconds (default: 20)")
    ap.add_argument("--receipt-poll-every", type=float, default=0.5, help="Receipt poll interval seconds (default: 0.5)")

    ap.add_argument("--progress-every", type=float, default=5.0, help="Progress log cadence in seconds (stderr)")

    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    # Sanity
    if args.rps <= 0:
        print("RPS must be > 0", file=sys.stderr)
        return 2
    if args.duration <= 0:
        print("Duration must be > 0", file=sys.stderr)
        return 2

    res = asyncio.run(run_soak(
        url=args.url,
        rps=args.rps,
        duration_s=args.duration,
        max_inflight=args.max_inflight,
        track_receipts=args.track_receipts,
        receipt_timeout_s=args.receipt_timeout,
        receipt_poll_every_s=args.receipt_poll_every,
        payload_dir=args.payload_dir,
        seed=int(args.seed),
        progress_every_s=args.progress_every,
    ))
    print(json.dumps(res, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
