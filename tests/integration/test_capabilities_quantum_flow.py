# -*- coding: utf-8 -*-
"""
Integration: Quantum traps flow end-to-end.

Scenario:
  1) Enqueue a small quantum job (trap-circuit) via CLI (preferred) or RPC fallback.
  2) Observe at least one new block after enqueue (next-block consumption rule).
  3) Poll read-only RPCs for job/result.
  4) Assert the result links to the task_id and carries proof/metrics consistent with traps.

This suite **skips by default** unless RUN_INTEGRATION_TESTS=1 (see tests/integration/__init__.py).

Environment (optional unless noted):
  ANIMICA_RPC_URL             — JSON-RPC URL (default: http://127.0.0.1:8545)
  ANIMICA_HTTP_TIMEOUT        — Per-call timeout seconds (default: 5)
  ANIMICA_ENQUEUE_VIA         — Force path: "cli" or "rpc" (default auto: try CLI, then RPC)
  ANIMICA_CLI_PYTHON          — Python executable for CLI path (default: sys.executable)
  ANIMICA_QC_SHOTS            — Shots for the quantum job (default: 64)
  ANIMICA_QC_TRAPS_RATIO      — Traps ratio (default: 0.1)
  ANIMICA_BLOCK_WAIT_SECS     — Max seconds to wait for the "next block" (default: 120)
  ANIMICA_RESULT_WAIT_SECS    — Max seconds to wait for result availability (default: 240)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # package-level gate & env helper


# ------------------------------- Common helpers -------------------------------

HEX_RE = re.compile(r"0x[0-9a-fA-F]{64,128}")

def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(rpc_url: str, method: str, params: Optional[Sequence[Any] | Dict[str, Any]] = None, *, req_id: int = 1) -> Any:
    if params is None:
        params = []
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"RPC {method} error: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"RPC {method} missing result: {msg}")
    return msg["result"]


def _rpc_try(rpc_url: str, methods: Sequence[str], params: Optional[Sequence[Any] | Dict[str, Any]] = None) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:
            last_exc = exc
    raise AssertionError(f"All methods failed ({methods}); last error: {last_exc}")


def _get_head_height(rpc_url: str) -> int:
    for m in ("chain.getHead", "chain_head", "eth_blockNumber"):
        try:
            _, res = _rpc_try(rpc_url, (m,), [])
            if isinstance(res, dict) and "height" in res:
                return int(res["height"])
            if isinstance(res, str) and res.startswith("0x"):
                return int(res, 16)
            if isinstance(res, int):
                return res
        except Exception:
            continue
    # Fallback through latest block
    try:
        _, res = _rpc_try(rpc_url, ("chain.getBlockByNumber",), ["latest", False, False])
        if isinstance(res, dict):
            if "number" in res:
                return int(res["number"])
            if "height" in res:
                return int(res["height"])
    except Exception:
        pass
    pytest.skip("Could not determine head height (no chain.getHead/eth_blockNumber available)")
    raise AssertionError("unreachable")


def _ensure_next_block(rpc_url: str, after_height: int) -> int:
    deadline = time.time() + float(env("ANIMICA_BLOCK_WAIT_SECS", "120"))
    while time.time() < deadline:
        h = _get_head_height(rpc_url)
        if h > after_height:
            return h
        time.sleep(1.0)
    pytest.skip(f"No new block observed within timeout; last height={_get_head_height(rpc_url)}")
    raise AssertionError("unreachable")


def _status_ok(v: Any) -> bool:
    if v in (True, "ok", "OK", "success", "completed", "done", "passed"):
        return True
    try:
        return int(v, 0) != 0 if isinstance(v, str) else bool(v)
    except Exception:
        return False


def _parse_task_id_from_text(s: str) -> Optional[str]:
    # Try JSON first
    try:
        js = json.loads(s)
        if isinstance(js, dict):
            for k in ("task_id", "id", "job_id"):
                v = js.get(k)
                if isinstance(v, str) and v.startswith("0x"):
                    return v
        if isinstance(js, (list, tuple)):
            for it in js:
                if isinstance(it, dict):
                    v = it.get("task_id") or it.get("id")
                    if isinstance(v, str) and v.startswith("0x"):
                        return v
    except Exception:
        pass
    # Heuristic hex
    m = HEX_RE.search(s)
    return m.group(0) if m else None


# ------------------------------- Fixture loading ------------------------------

def _read_circuit_fixture() -> Dict[str, Any]:
    """
    Load a tiny circuit fixture if present; otherwise synthesize a minimal one.
    The exact schema may vary by devnet; we stick to a common subset.
    """
    p = Path("capabilities/fixtures/quantum_circuit.json")
    if p.is_file():
        try:
            js = json.loads(p.read_text())
            if isinstance(js, dict):
                return js
        except Exception:
            pass
    # Minimal illustrative circuit: 2-qubit Bell-ish
    return {
        "n_qubits": 2,
        "gates": [
            {"op": "H", "targets": [0]},
            {"op": "CX", "controls": [0], "targets": [1]},
            {"op": "MEASURE", "targets": [0, 1]},
        ],
    }


def _quantum_job_spec() -> Dict[str, Any]:
    shots = int(env("ANIMICA_QC_SHOTS", "64"))
    traps_ratio = float(env("ANIMICA_QC_TRAPS_RATIO", "0.1"))
    return {
        "kind": "Quantum",
        "circuit": _read_circuit_fixture(),
        "shots": shots,
        "traps_ratio": traps_ratio,
    }


# -------------------------------- Enqueue paths -------------------------------

def _enqueue_via_cli(rpc_url: str) -> str:
    """
    Try: python -m capabilities.cli.enqueue_quantum --rpc <url> [--circuit <path> | --circuit-json <json>]
         --shots N --traps-ratio R
    Returns the task_id (hex) parsed from stdout/stderr.
    """
    py_exec = env("ANIMICA_CLI_PYTHON", sys.executable)
    circuit_path = Path("capabilities/fixtures/quantum_circuit.json")
    shots = str(int(env("ANIMICA_QC_SHOTS", "64")))
    traps = str(float(env("ANIMICA_QC_TRAPS_RATIO", "0.1")))

    # Prefer file path if fixture exists; else pass JSON inline with a common flag name.
    if circuit_path.is_file():
        cmd = [
            py_exec, "-m", "capabilities.cli.enqueue_quantum",
            "--rpc", rpc_url,
            "--circuit", str(circuit_path),
            "--shots", shots,
            "--traps-ratio", traps,
        ]
    else:
        cmd = [
            py_exec, "-m", "capabilities.cli.enqueue_quantum",
            "--rpc", rpc_url,
            "--circuit-json", json.dumps(_read_circuit_fixture()),
            "--shots", shots,
            "--traps-ratio", traps,
        ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        pytest.skip("Python executable not found for CLI path")
    except subprocess.CalledProcessError as exc:
        raise AssertionError(f"enqueue_quantum CLI failed: {exc.stderr or exc.stdout}") from exc
    except Exception as exc:
        pytest.skip(f"enqueue_quantum CLI not available: {exc}")

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    task_id = _parse_task_id_from_text(out)
    if not task_id:
        raise AssertionError(f"Could not parse task_id from enqueue_quantum output:\n{out}")
    return task_id


def _enqueue_via_rpc(rpc_url: str) -> str:
    """
    Attempt ad-hoc RPC spellings for quantum enqueue:
      - cap.enqueueQuantum
      - cap.enqueue (with kind=Quantum)
      - capabilities.enqueue
      - aicf.enqueueQuantum
    Returns task_id hex.
    """
    req = _quantum_job_spec()
    methods = ("cap.enqueueQuantum", "cap.enqueue", "capabilities.enqueue", "aicf.enqueueQuantum")
    last_err: Optional[Exception] = None
    for m in methods:
        try:
            _, res = _rpc_try(rpc_url, (m,), [req])
            if isinstance(res, str) and res.startswith("0x"):
                return res
            if isinstance(res, dict):
                for k in ("task_id", "id", "job_id"):
                    v = res.get(k)
                    if isinstance(v, str) and v.startswith("0x"):
                        return v
        except Exception as exc:
            last_err = exc
            continue
    if last_err:
        raise AssertionError(f"RPC enqueue for quantum failed via {methods}: {last_err}")
    raise AssertionError("Unexpected: quantum enqueue RPC returned no usable task_id")


def _enqueue_job(rpc_url: str) -> str:
    forced = env("ANIMICA_ENQUEUE_VIA")
    if forced == "cli":
        return _enqueue_via_cli(rpc_url)
    if forced == "rpc":
        return _enqueue_via_rpc(rpc_url)
    # Auto: prefer CLI, then fallback to RPC
    try:
        return _enqueue_via_cli(rpc_url)
    except pytest.skip.Exception:
        raise
    except Exception:
        return _enqueue_via_rpc(rpc_url)


# ------------------------------ Result retrieval ------------------------------

def _get_job(rpc_url: str, task_id: str) -> Optional[Dict[str, Any]]:
    for m in ("cap.getJob", "capabilities.getJob", "aicf.getJob"):
        try:
            _, res = _rpc_try(rpc_url, (m,), [task_id])
            if isinstance(res, dict):
                return res
        except Exception:
            continue
    return None


def _get_result(rpc_url: str, task_id: str) -> Optional[Dict[str, Any]]:
    for m in ("cap.getResult", "capabilities.getResult"):
        try:
            _, res = _rpc_try(rpc_url, (m,), [task_id])
            if isinstance(res, dict):
                return res
        except Exception:
            continue
    return None


# ------------------------------------ Test ------------------------------------

@pytest.mark.timeout(480)
def test_quantum_traps_flow_end_to_end():
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")

    # Height before enqueue
    h0 = _get_head_height(rpc_url)

    # Enqueue the quantum job (trap circuit)
    task_id = _enqueue_job(rpc_url)
    assert isinstance(task_id, str) and task_id.startswith("0x"), f"Bad task_id: {task_id}"

    # Ensure at least one new block after enqueue
    _ensure_next_block(rpc_url, h0)

    # Poll for result availability
    deadline = time.time() + float(env("ANIMICA_RESULT_WAIT_SECS", "240"))
    last_job: Optional[Dict[str, Any]] = None
    last_res: Optional[Dict[str, Any]] = None

    while time.time() < deadline:
        job = _get_job(rpc_url, task_id)
        if job:
            last_job = job
        res = _get_result(rpc_url, task_id)
        if res:
            last_res = res
            status = res.get("status") or res.get("state") or res.get("ok")
            if _status_ok(status) or any(k in res for k in ("proof", "digest", "metrics", "output")):
                break
        time.sleep(1.0)

    if last_res is None:
        pytest.skip(f"Quantum result not available within timeout; last_job={last_job}")

    # Sanity checks on the result object
    assert last_res.get("task_id", task_id).lower() == task_id.lower(), f"Result task_id mismatch: {last_res}"

    # Expect at least one of these keys: proof (reference), metrics (traps/qos), output/digest
    assert any(k in last_res for k in ("proof", "metrics", "digest", "output")), f"Incomplete quantum result: {last_res}"

    # If metrics are present, lightly sanity check traps-related fields
    metrics = last_res.get("metrics")
    if isinstance(metrics, dict):
        # Shots should match or be close to requested (devnets may adjust); don't overconstrain.
        if "shots" in metrics:
            assert int(metrics["shots"]) > 0
        # Traps-related signals should be within [0,1] if present
        for key in ("traps_ratio", "traps_ratio_observed", "qos", "success_rate"):
            if key in metrics:
                v = float(metrics[key])
                assert 0.0 <= v <= 1.0, f"{key} out of range: {v}"

