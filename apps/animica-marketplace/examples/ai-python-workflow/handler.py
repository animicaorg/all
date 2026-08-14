# ai-python-workflow ("chain-pulse") — fetch -> transform (pandas/numpy) -> summarize with AI.
#
# The full data-workflow shape on Animica Python Cloud:
#   1. FETCH    animica.http.fetch (HTTP_FETCH capability) — the sandbox itself has no
#               network, so the host performs the request and SSRF-guards it. Here: the
#               public Animica JSON-RPC endpoint, pulling the chain head and a window of
#               recent blocks.
#   2. TRANSFORM pandas + numpy from the curated sandbox package set: a DataFrame of block
#               heights/timestamps, numpy for block-interval statistics.
#   3. SUMMARIZE animica.ai.infer (AI_INFERENCE capability) turns the numbers into a
#               one-paragraph narrative. If the miner network cannot serve inference right
#               now, the function composes a deterministic report from the same real numbers
#               and says so in the `engine` field — the data analysis stands on its own.

import json
import os

# Cap BLAS thread pools BEFORE importing numpy: the sandbox caps processes/threads hard
# (RLIMIT_NPROC / cgroup pids), and OpenBLAS otherwise tries to spawn one thread per core at
# import time and fails. Single-threaded math is the right shape for a small sandbox anyway.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd

import animica

RPC_URL = "https://rpc.animica.org/rpc"


def _rpc(method, params):
    res = animica.http.fetch(
        RPC_URL,
        method="POST",
        headers={"content-type": "application/json"},
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
        timeout=15,
    )
    if res.get("status") != 200:
        raise RuntimeError(f"rpc {method} returned http {res.get('status')}")
    data = json.loads(res.get("body") or "{}")
    if data.get("error"):
        raise RuntimeError(f"rpc {method}: {data['error'].get('message', 'error')}")
    return data.get("result") or {}


def main(request, ctx):
    req = request if isinstance(request, dict) else {}
    window = max(4, min(int(req.get("blocks", 12)), 24))

    head = _rpc("chain.getHead", [])
    tip = int(head.get("height") or head.get("number") or 0)
    if tip < window:
        raise RuntimeError(f"chain height {tip} is smaller than the requested window")

    rows = []
    for h in range(tip - window + 1, tip + 1):
        b = _rpc("chain.getBlockByNumber", [h])
        rows.append({"height": h, "timestamp": int(b.get("timestamp") or 0)})
    animica.log(f"fetched {len(rows)} blocks up to height {tip}")

    df = pd.DataFrame(rows).sort_values("height")
    intervals = df["timestamp"].diff().dropna().to_numpy(dtype=float)
    stats = {
        "blocks": int(len(df)),
        "mean_s": round(float(np.mean(intervals)), 1),
        "median_s": round(float(np.median(intervals)), 1),
        "min_s": int(np.min(intervals)),
        "max_s": int(np.max(intervals)),
    }

    facts = (
        f"Animica chain tip is {tip}. Over the last {stats['blocks']} blocks the block "
        f"interval averaged {stats['mean_s']}s (median {stats['median_s']}s, "
        f"min {stats['min_s']}s, max {stats['max_s']}s)."
    )

    try:
        narrative = animica.ai.infer(
            "You are a blockchain analyst. In one short paragraph, describe the network "
            "cadence these measurements show. Facts: " + facts,
            max_tokens=180,
        ).strip()
        engine = "animica-ai"
    except animica.AnimicaError as exc:
        animica.log("ai.infer unavailable, deterministic narrative:", str(exc)[:160], level="warn")
        narrative = facts
        engine = "deterministic-fallback"

    return {
        "tip_height": tip,
        "window": window,
        "block_time_seconds": stats,
        "narrative": narrative,
        "engine": engine,
        "request_id": ctx.request_id,
    }
