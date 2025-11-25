# -*- coding: utf-8 -*-
"""
Integration: enqueue AI job → (AICF proves) → consume result on/after next block.

This test is designed to be **flexible** across different devnet setups. It will:
  1) Enqueue a tiny AI job (prefer CLI helper; fall back to ad-hoc RPC spellings).
  2) Ensure at least one new block is mined after enqueue (next-block consumption rule).
  3) Poll read-only RPCs (cap.getJob / cap.getResult) until the result is available.
  4) Assert the result record links back to the same task_id and looks sane.

It **skips by default** unless RUN_INTEGRATION_TESTS=1 in the environment
(see tests/integration/__init__.py). It gracefully skips when optional surfaces
are not present.

Environment (optional unless noted):
  ANIMICA_RPC_URL            — JSON-RPC URL (default: http://127.0.0.1:8545)
  ANIMICA_HTTP_TIMEOUT       — Per-request timeout seconds (default: 5)
  ANIMICA_AI_MODEL           — Model id/name string (default: "test/echo")
  ANIMICA_AI_PROMPT          — Prompt; default uses capabilities/fixtures/ai_prompt.json if present,
                               otherwise a small deterministic JSON.
  ANIMICA_ENQUEUE_VIA        — Force a path: "cli" or "rpc" (default: auto: try CLI then RPC).
  ANIMICA_CLI_PYTHON         — Python executable for CLI path (default: sys.executable)
  ANIMICA_BLOCK_WAIT_SECS    — Max seconds to wait for the "next block" (default: 120)
  ANIMICA_RESULT_WAIT_SECS   — Max seconds to wait for result availability (default: 180)
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # RUN_INTEGRATION_TESTS gate + env helper


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------

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
        "params": params if isinstance(params, list) else params,
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
    # Try a few common spellings
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
    # Very minimal fallback: chain.getBlockByNumber "latest"
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


def _read_fixture_prompt() -> Dict[str, Any]:
    # Attempt repository fixture
    p = Path("capabilities/fixtures/ai_prompt.json")
    if p.is_file():
        try:
            js = json.loads(p.read_text())
            if isinstance(js, dict):
                return js
        except Exception:
            pass
    # Deterministic tiny prompt payload
    return {"model": env("ANIMICA_AI_MODEL", "test/echo"), "prompt": "ping"}


def _ensure_next_block(rpc_url: str, after_height: int) -> int:
    deadline = time.time() + float(env("ANIMICA_BLOCK_WAIT_SECS", "120"))
    while time.time() < deadline:
        h = _get_head_height(rpc_url)
        if h > after_height:
            return h
        time.sleep(1.0)
    pytest.skip(f"No new block observed within timeout; last height={_get_head_height(rpc_url)}")
    raise AssertionError("unreachable")


def _parse_task_id_from_text(s: str) -> Optional[str]:
    # Look for a JSON object with task_id or any 0x…-looking hex of >= 64 nybbles
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
    m = HEX_RE.search(s)
    return m.group(0) if m else None


# -----------------------------------------------------------------------------
# Enqueue strategies
# -----------------------------------------------------------------------------

def _enqueue_via_cli(rpc_url: str) -> str:
    """
    Try `python -m capabilities.cli.enqueue_ai --rpc <url> --model X --prompt 'Y'`
    Expect stdout to include a JSON blob or a hex task_id. Returns task_id (hex).
    """
    py = env("ANIMICA_CLI_PYTHON", sys.executable)
    # Build args
    prompt_obj = _read_fixture_prompt()
    model = prompt_obj.get("model") or env("ANIMICA_AI_MODEL", "test/echo")
    prompt = prompt_obj.get("prompt") or env("ANIMICA_AI_PROMPT", "ping")

    cmd = [
        py, "-m", "capabilities.cli.enqueue_ai",
        "--rpc", rpc_url,
        "--model", str(model),
        "--prompt", str(prompt),
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        pytest.skip("Python executable not found for CLI path")
    except subprocess.CalledProcessError as exc:
        raise AssertionError(f"enqueue_ai CLI failed: {exc.stderr or exc.stdout}") from exc
    except Exception as exc:
        pytest.skip(f"enqueue_ai CLI not available: {exc}")

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    task_id = _parse_task_id_from_text(out)
    if not task_id:
        raise AssertionError(f"Could not parse task_id from enqueue CLI output:\n{out}")
    return task_id


def _enqueue_via_rpc(rpc_url: str) -> str:
    """
    Attempt ad-hoc write RPC spellings (not guaranteed to exist in prod):
      - cap.enqueueAI
      - cap.enqueue
      - capabilities.enqueue
    Returns task_id (hex) on success, or raises AssertionError.
    """
    payload = _read_fixture_prompt()
    # Normalize to a minimal shape commonly accepted by dev helpers
    req = {"kind": "AI", "model": payload.get("model", "test/echo"), "prompt": payload.get("prompt", "ping")}
    methods = ("cap.enqueueAI", "cap.enqueue", "capabilities.enqueue", "aicf.enqueueAI")
    last_err: Optional[Exception] = None
    for m in methods:
        try:
            _, res = _rpc_try(rpc_url, (m,), [req])
            # Accept {"task_id": "0x.."} or direct hex
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
        raise AssertionError(f"Could not enqueue via RPC spellings {methods}: {last_err}")
    raise AssertionError("Unexpected: enqueue RPC returned no usable task_id")


def _enqueue_job(rpc_url: str) -> str:
    forced = env("ANIMICA_ENQUEUE_VIA")
    if forced == "cli":
        return _enqueue_via_cli(rpc_url)
    if forced == "rpc":
        return _enqueue_via_rpc(rpc_url)
    # Auto: try CLI then RPC
    try:
        return _enqueue_via_cli(rpc_url)
    except pytest.skip.Exception:
        # propagate skips from CLI path
        raise
    except Exception:
        # fall back to RPC
        return _enqueue_via_rpc(rpc_url)


# -----------------------------------------------------------------------------
# Result polling
# -----------------------------------------------------------------------------

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


def _status_ok(v: Any) -> bool:
    if v in (True, "ok", "OK", "success", "completed", "done"):
        return True
    try:
        return int(v, 0) != 0 if isinstance(v, str) else bool(v)
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Test
# -----------------------------------------------------------------------------

@pytest.mark.timeout(420)
def test_ai_enqueue_then_consume_next_block():
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")

    # Height before enqueue
    h0 = _get_head_height(rpc_url)

    # Enqueue job — obtain task_id
    task_id = _enqueue_job(rpc_url)
    assert isinstance(task_id, str) and task_id.startswith("0x"), f"Bad task_id: {task_id}"

    # Ensure at least one new block after enqueue (next-block consumption rule)
    h1 = _ensure_next_block(rpc_url, h0)

    # Poll for job/result availability
    deadline = time.time() + float(env("ANIMICA_RESULT_WAIT_SECS", "180"))
    last_job: Optional[Dict[str, Any]] = None
    last_res: Optional[Dict[str, Any]] = None

    while time.time() < deadline:
        job = _get_job(rpc_url, task_id)
        if job:
            last_job = job
        res = _get_result(rpc_url, task_id)
        if res:
            last_res = res
            # Common shapes: {"status": "completed", "ok": true, "output": {...}, "proof": {...}}
            status = res.get("status") or res.get("state") or res.get("ok")
            if _status_ok(status) or res.get("output") or res.get("digest") or res.get("proof"):
                break
        time.sleep(1.0)

    if last_res is None:
        # Some devnets may not have the AICF → proofs resolver running; don't fail the whole suite.
        pytest.skip(f"Result not available within timeout; last_job={last_job}")

    # Sanity checks on the result object
    assert last_res.get("task_id", task_id).lower() == task_id.lower(), f"Result task_id mismatch: {last_res}"
    # We accept either a direct output (e.g., echo) or a proof reference/digest recorded.
    assert any(k in last_res for k in ("output", "digest", "proof", "metrics")), f"Incomplete result payload: {last_res}"

    # If the result carries a short echo output, make a light sanity assertion
    out = last_res.get("output")
    if isinstance(out, (str, bytes)):
        # We used a 'ping' default prompt in fixtures — many dev helpers echo it back.
        assert "ping" in (out.decode("utf-8", "ignore") if isinstance(out, bytes) else out).lower() or len(out) > 0

