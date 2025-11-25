#!/usr/bin/env python3
"""
ai_enqueue_then_consume.py — AICF flow demo

This example shows how to:
  1) Connect to a node via HTTP JSON-RPC
  2) Enqueue a small AI job with AICF
  3) Poll for completion and fetch the result
  4) Print a concise summary (tokens, latency, costs if provided)

Assumptions
-----------
- Your node/devnet runs with AICF enabled and exposes the AICF RPC surface.
- Methods used may include:
    - aicf.listProviders / aicf.getProvider (optional sanity)
    - aicf.listJobs / aicf.getJob
    - cap.getResult (capabilities read-only result fetch)
- On some setups, `enqueue` may be dev-only and not exposed. This demo falls back
  to a generic `aicf.enqueueJob` method name if present. See notes in `_enqueue_ai`.

Environment
-----------
OMNI_SDK_RPC_URL        (default: http://127.0.0.1:8545)
OMNI_SDK_HTTP_TIMEOUT   (default: 30)

Usage
-----
python sdk/python/examples/ai_enqueue_then_consume.py \
  --model echo-small \
  --prompt "Hello from AICF!"

"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional

from omni_sdk.rpc.http import RpcClient
from omni_sdk.aicf.client import AICFClient


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _enqueue_ai(aicf: AICFClient, model: str, prompt: str, **kwargs: Any) -> Dict[str, Any]:
    """
    Enqueue an AI job via AICFClient. The AICFClient abstracts over possible
    RPC differences; if your node exposes a specific enqueue method, make sure
    AICFClient maps to it. This function keeps a single call-site.
    """
    try:
        return aicf.enqueue_ai(model=model, prompt=prompt, **kwargs)
    except Exception as e:
        # Best-effort fallback: try a generic RPC if the client didn't implement enqueue.
        try:
            # This path calls directly using the underlying RpcClient contract.
            # Method name may differ depending on your node; adjust if needed.
            # We keep the envelope simple: {"kind":"AI","model":..., "prompt":...}
            rpc: RpcClient = aicf._rpc  # type: ignore[attr-defined]
            payload = {"kind": "AI", "model": model, "prompt": prompt}
            payload.update(kwargs)
            job = rpc.call("aicf.enqueueJob", [payload])
            if isinstance(job, dict):
                return job
        except Exception:
            pass
        raise RuntimeError(f"Failed to enqueue AI job: {e}") from e


def _wait_for_result(aicf: AICFClient, job_id: str, timeout_s: float = 120.0, poll_s: float = 1.5) -> Dict[str, Any]:
    """
    Poll AICF for job completion, then fetch the result via AICF or capabilities.
    """
    deadline = time.time() + timeout_s
    last_status = None
    while True:
        # Prefer AICF get_job
        try:
            job = aicf.get_job(job_id)
            last_status = job.get("status")
            if last_status in ("Completed", "FAILED", "Cancelled", "Slashed"):
                break
        except Exception:
            # Fall back silently; we'll try fetching result regardless
            pass

        # Attempt direct result read (some nodes expose result as soon as ready)
        try:
            res = aicf.get_result(job_id)
            if res:
                return {"job": job if 'job' in locals() else {"id": job_id, "status": "Completed"}, "result": res}
        except Exception:
            pass

        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for job {job_id} (last status: {last_status})")
        time.sleep(poll_s)

    # Completed/terminal. Fetch result (may raise if job failed).
    result = aicf.get_result(job_id)
    return {"job": job, "result": result}  # type: ignore[name-defined]


def main() -> None:
    ap = argparse.ArgumentParser(description="AICF AI enqueue → result demo")
    ap.add_argument("--rpc", default=os.getenv("OMNI_SDK_RPC_URL", "http://127.0.0.1:8545"), help="RPC HTTP URL")
    ap.add_argument("--timeout", type=float, default=float(os.getenv("OMNI_SDK_HTTP_TIMEOUT", "30")), help="HTTP timeout (s)")
    ap.add_argument("--model", default="echo-small", help="Model name (node-specific registry)")
    ap.add_argument("--prompt", default="Hello, Animica AICF!", help="Prompt to send")
    ap.add_argument("--max-wait", type=float, default=120.0, help="Max seconds to wait for completion")
    ap.add_argument("--poll", type=float, default=1.5, help="Polling interval in seconds")
    ap.add_argument("--units", type=int, default=None, help="Optional model units/budget")
    args = ap.parse_args()

    rpc = RpcClient(args.rpc, timeout=args.timeout)
    aicf = AICFClient(rpc)

    print("== AICF: enqueue AI job ==")
    print(f"rpc={args.rpc} model={args.model}")
    job = _enqueue_ai(aicf, model=args.model, prompt=args.prompt, units=args.units)
    job_id = str(job.get("id") or job.get("jobId") or job.get("job_id") or "")
    if not job_id:
        _print_json(job)
        sys.exit("error: enqueue did not return a job id")

    print(f"enqueued job: {job_id}")
    if "status" in job:
        print(f"initial status: {job['status']}")

    print("== waiting for result ==")
    try:
        out = _wait_for_result(aicf, job_id, timeout_s=args.max_wait, poll_s=args.poll)
    except TimeoutError as e:
        sys.exit(f"error: {e}")

    job_info = out["job"]
    result = out["result"]

    print("== job ==")
    _print_json(job_info)

    # Result payloads may vary by node/provider. Common shapes include:
    # { "text": "...", "tokens": N, "latencyMs": M, "cost": {...}, "digest": "..." }
    print("== result ==")
    _print_json(result)

    # Friendly summary (best-effort)
    text = result.get("text") or result.get("output") or result.get("message")
    tokens = result.get("tokens") or result.get("usage", {}).get("total_tokens")
    latency = result.get("latencyMs") or result.get("latency_ms")
    cost = result.get("cost") or {}
    summary = {
        "jobId": job_id,
        "status": job_info.get("status"),
        "text_preview": (text[:120] + "…") if isinstance(text, str) and len(text) > 120 else text,
        "tokens": tokens,
        "latencyMs": latency,
        "cost": cost,
    }
    print("== summary ==")
    _print_json(summary)


if __name__ == "__main__":
    main()
