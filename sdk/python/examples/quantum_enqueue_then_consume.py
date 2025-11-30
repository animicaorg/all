#!/usr/bin/env python3
"""
quantum_enqueue_then_consume.py — AICF Quantum flow demo

This example shows how to:
  1) Connect to a node via HTTP JSON-RPC
  2) Enqueue a small Quantum job (trap/circuit) with AICF
  3) Poll for completion and fetch the result
  4) Print a concise summary (counts/probabilities, latency, costs if provided)

Assumptions
-----------
- Your node/devnet runs with AICF enabled and exposes the AICF RPC surface.
- Methods used may include:
    - aicf.listProviders / aicf.getProvider (optional sanity)
    - aicf.listJobs / aicf.getJob
    - cap.getResult (capabilities read-only result fetch)
- On some setups, `enqueue` may be dev-only and not exposed. This demo falls back
  to a generic `aicf.enqueueJob` method name if present. See notes in `_enqueue_quantum`.

Environment
-----------
OMNI_SDK_RPC_URL        (default: http://127.0.0.1:8545)
OMNI_SDK_HTTP_TIMEOUT   (default: 30)

Usage
-----
# Use a built-in "bell" preset (2-qubit Bell state)
python sdk/python/examples/quantum_enqueue_then_consume.py --preset bell --shots 512

# Or provide a circuit JSON file (schema is node-specific; a common shape is shown below)
python sdk/python/examples/quantum_enqueue_then_consume.py --circuit-file ./bell.json --shots 512

Example circuit JSON (illustrative):
{
  "qubits": 2,
  "ops": [
    {"gate":"H","target":0},
    {"gate":"CNOT","control":0,"target":1}
  ],
  "measure": [0,1]
}
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from omni_sdk.aicf.client import AICFClient
from omni_sdk.rpc.http import RpcClient


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _bell_circuit() -> Dict[str, Any]:
    # A minimal, widely used example; structure kept intentionally generic.
    return {
        "qubits": 2,
        "ops": [
            {"gate": "H", "target": 0},
            {"gate": "CNOT", "control": 0, "target": 1},
        ],
        "measure": [0, 1],
        "name": "Bell(00+11)/sqrt(2)",
    }


def _load_circuit_from_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        sys.exit(f"error: circuit file not found: {path}")
    except Exception as e:
        sys.exit(f"error: invalid circuit JSON at {path}: {e}")


def _enqueue_quantum(
    aicf: AICFClient, circuit: Dict[str, Any], shots: int, **kwargs: Any
) -> Dict[str, Any]:
    """
    Enqueue a Quantum job via AICFClient. The AICFClient abstracts over possible
    RPC differences; if your node exposes a specific enqueue method, make sure
    AICFClient maps to it. This function keeps a single call-site.
    """
    try:
        return aicf.enqueue_quantum(circuit=circuit, shots=shots, **kwargs)
    except Exception as e:
        # Best-effort fallback: try a generic RPC if the client didn't implement enqueue.
        try:
            rpc: RpcClient = aicf._rpc  # type: ignore[attr-defined]
            payload = {"kind": "Quantum", "circuit": circuit, "shots": shots}
            payload.update(kwargs)
            job = rpc.call("aicf.enqueueJob", [payload])
            if isinstance(job, dict):
                return job
        except Exception:
            pass
        raise RuntimeError(f"Failed to enqueue Quantum job: {e}") from e


def _wait_for_result(
    aicf: AICFClient, job_id: str, timeout_s: float = 180.0, poll_s: float = 2.0
) -> Dict[str, Any]:
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
            pass

        # Attempt direct result read (some nodes expose result as soon as ready)
        try:
            res = aicf.get_result(job_id)
            if res:
                return {
                    "job": (
                        job
                        if "job" in locals()
                        else {"id": job_id, "status": "Completed"}
                    ),
                    "result": res,
                }
        except Exception:
            pass

        if time.time() > deadline:
            raise TimeoutError(
                f"Timed out waiting for job {job_id} (last status: {last_status})"
            )
        time.sleep(poll_s)

    # Completed/terminal. Fetch result (may raise if job failed).
    result = aicf.get_result(job_id)
    return {"job": job, "result": result}  # type: ignore[name-defined]


def _counts_and_probs(
    result: Dict[str, Any],
) -> Tuple[Dict[str, int], Dict[str, float]]:
    """
    Extract measurement counts from a variety of common result shapes and compute probabilities.
    Expected shapes (examples):
      { "counts": {"00": 260, "11": 252} }
      { "histogram": {"00": 0.51, "11": 0.49}, "shots": 512 }
      { "bitstrings": ["00","11","00",...]}  # we will aggregate
    """
    counts: Dict[str, int] = {}

    if "counts" in result and isinstance(result["counts"], dict):
        for k, v in result["counts"].items():
            try:
                counts[str(k)] = int(v)
            except Exception:
                continue
    elif "bitstrings" in result and isinstance(result["bitstrings"], list):
        for s in result["bitstrings"]:
            key = str(s)
            counts[key] = counts.get(key, 0) + 1
    elif "histogram" in result and isinstance(result["histogram"], dict):
        shots = int(result.get("shots") or 0)
        if shots <= 0:
            # Try to infer shots from probabilities by scaling to a round number
            shots = 1000
        for k, p in result["histogram"].items():
            try:
                c = int(round(float(p) * shots))
                counts[str(k)] = c
            except Exception:
                continue

    total = sum(counts.values())
    probs: Dict[str, float] = {}
    if total > 0:
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            probs[k] = v / total
    return counts, probs


def _format_probs(probs: Dict[str, float]) -> str:
    if not probs:
        return "(no probabilities)"
    rows = []
    for bitstr, p in sorted(probs.items(), key=lambda kv: (-kv[1], kv[0])):
        rows.append(f"  {bitstr}: {p:.4f}")
    return "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="AICF Quantum enqueue → result demo")
    ap.add_argument(
        "--rpc",
        default=os.getenv("OMNI_SDK_RPC_URL", "http://127.0.0.1:8545"),
        help="RPC HTTP URL",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("OMNI_SDK_HTTP_TIMEOUT", "30")),
        help="HTTP timeout (s)",
    )
    group = ap.add_mutually_exclusive_group()
    group.add_argument(
        "--preset", choices=["bell"], default="bell", help="Built-in circuit preset"
    )
    group.add_argument("--circuit-file", type=Path, help="Path to circuit JSON file")
    ap.add_argument("--shots", type=int, default=512, help="Number of shots/samples")
    ap.add_argument(
        "--max-wait",
        type=float,
        default=180.0,
        help="Max seconds to wait for completion",
    )
    ap.add_argument(
        "--poll", type=float, default=2.0, help="Polling interval in seconds"
    )
    ap.add_argument(
        "--units", type=int, default=None, help="Optional quantum units/budget"
    )
    args = ap.parse_args()

    # Circuit
    if args.circuit_file:
        circuit = _load_circuit_from_file(args.circuit_file)
    else:
        if args.preset == "bell":
            circuit = _bell_circuit()
        else:
            sys.exit(f"unknown preset: {args.preset}")

    rpc = RpcClient(args.rpc, timeout=args.timeout)
    aicf = AICFClient(rpc)

    print("== AICF: enqueue Quantum job ==")
    print(f"rpc={args.rpc} shots={args.shots}")
    job = _enqueue_quantum(aicf, circuit=circuit, shots=args.shots, units=args.units)
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

    print("== raw result ==")
    _print_json(result)

    counts, probs = _counts_and_probs(result)
    total = sum(counts.values())
    latency = result.get("latencyMs") or result.get("latency_ms")
    cost = result.get("cost") or {}
    summary = {
        "jobId": job_id,
        "status": job_info.get("status"),
        "shots": args.shots,
        "totalCounts": total,
        "latencyMs": latency,
        "cost": cost,
        "topOutcomes": sorted(
            [
                {"bitstring": k, "count": v, "p": probs.get(k, 0.0)}
                for k, v in counts.items()
            ],
            key=lambda x: -x["count"],
        )[:8],
    }
    print("== summary ==")
    _print_json(summary)

    print("== probabilities ==")
    print(_format_probs(probs))


if __name__ == "__main__":
    main()
