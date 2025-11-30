# -*- coding: utf-8 -*-
"""
Integration: AICF settlement — provider proof → payout → balance reflects.

Strategy (robust to devnet variations):
  1) Discover at least one provider via AICF RPC (or skip if none).
  2) Record provider balance before.
  3) Find a *completed* job (AI or Quantum) eligible for payout:
       - Prefer one assigned to this provider and in a completed/claimable state.
       - If none are found, attempt to enqueue a tiny AI job via capabilities RPC/CLI
         and wait for completion (best-effort; will skip if not available).
  4) Call aicf.claimPayout(job/task id) (or equivalent) and wait until provider balance increases
     or the job shows a "settled/paid" state.
  5) Assert that some monotone balance component increased.

This suite **skips by default** unless RUN_INTEGRATION_TESTS=1 (see tests/integration/__init__.py).

Environment:
  ANIMICA_RPC_URL            — JSON-RPC URL (default: http://127.0.0.1:8545)
  ANIMICA_HTTP_TIMEOUT       — Per-call timeout seconds (default: 5)
  ANIMICA_RESULT_WAIT_SECS   — Wait for job/result (default: 240)
  ANIMICA_SETTLE_WAIT_SECS   — Wait for claim → balance reflect (default: 180)
  ANIMICA_ENQUEUE_VIA        — "cli" or "rpc" to enqueue when needed (default auto)
  ANIMICA_CLI_PYTHON         — Python executable for CLI path (default: sys.executable)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # gate + env helper

# -----------------------------------------------------------------------------
# RPC helpers
# -----------------------------------------------------------------------------


def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(
    rpc_url: str,
    method: str,
    params: Optional[Sequence[Any] | Dict[str, Any]] = None,
    *,
    req_id: int = 1,
) -> Any:
    if params is None:
        params = []
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
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


def _rpc_try(
    rpc_url: str,
    methods: Sequence[str],
    params: Optional[Sequence[Any] | Dict[str, Any]] = None,
) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:
            last_exc = exc
    raise AssertionError(f"All methods failed ({methods}); last error: {last_exc}")


# -----------------------------------------------------------------------------
# AICF discovery helpers
# -----------------------------------------------------------------------------


def _list_providers(rpc_url: str) -> List[Dict[str, Any]]:
    methods = ("aicf.listProviders", "aicf_providers", "aicf/providers/list")
    try:
        for m in methods:
            try:
                res = _rpc_call(rpc_url, m, [])
                if isinstance(res, list):
                    # ensure dicts
                    return [x for x in res if isinstance(x, dict)]
            except Exception:
                continue
    except Exception as exc:
        pytest.skip(f"AICF listProviders not available: {exc}")
    return []


def _provider_id(p: Dict[str, Any]) -> Optional[str]:
    for k in ("id", "provider_id", "providerId", "pk", "address"):
        v = p.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, int):
            return str(v)
    return None


def _get_balance(rpc_url: str, provider_id: str) -> Optional[Dict[str, Any]]:
    # Prefer explicit getBalance
    try_methods = [
        ("aicf.getBalance", [provider_id]),
        ("aicf.getProvider", [provider_id]),
    ]
    for m, params in try_methods:
        try:
            res = _rpc_call(rpc_url, m, params)
            if isinstance(res, dict):
                return res
        except Exception:
            continue
    return None


def _extract_balance_numbers(bal: Dict[str, Any]) -> Dict[str, int]:
    """
    Extract potentially present numeric balance fields, normalized to integers.
    Accept {available, locked, pending, total, withdrawable} (any subset).
    """
    out: Dict[str, int] = {}
    for k in (
        "available",
        "locked",
        "pending",
        "total",
        "withdrawable",
        "escrow",
        "rewards",
    ):
        v = bal.get(k)
        if isinstance(v, (int, float)):
            out[k] = int(v)
        elif isinstance(v, str):
            try:
                # accept hex or decimal
                out[k] = int(v, 16) if v.startswith("0x") else int(v)
            except Exception:
                continue
    return out


def _balance_score(bal: Dict[str, Any]) -> int:
    """
    Reduce a balance dict to a single comparable score.
    Prefer 'available' if present; else sum of known fields.
    """
    nums = _extract_balance_numbers(bal)
    if "available" in nums:
        return nums["available"]
    return sum(nums.values()) if nums else 0


# -----------------------------------------------------------------------------
# Job helpers
# -----------------------------------------------------------------------------


def _list_jobs_any(
    rpc_url: str, provider_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Try to list jobs via aicf.listJobs; accept optional provider filter (shape may vary).
    """
    candidates: List[Dict[str, Any]] = []
    # Try plain list
    try:
        res = _rpc_call(rpc_url, "aicf.listJobs", [])
        if isinstance(res, list):
            candidates.extend([x for x in res if isinstance(x, dict)])
    except Exception:
        pass

    # Try filter variants
    filter_variants: List[Dict[str, Any]] = []
    if provider_id:
        filter_variants = [
            {"provider_id": provider_id},
            {"providerId": provider_id},
            {"provider": provider_id},
            {"filter": {"provider_id": provider_id}},
        ]
    for flt in filter_variants:
        try:
            res = _rpc_call(rpc_url, "aicf.listJobs", [flt])
            if isinstance(res, list):
                candidates.extend([x for x in res if isinstance(x, dict)])
        except Exception:
            continue

    # Deduplicate by id
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for j in candidates:
        jid = _job_id(j)
        if jid and jid not in seen:
            seen.add(jid)
            out.append(j)
    return out


def _job_id(j: Dict[str, Any]) -> Optional[str]:
    for k in ("id", "job_id", "task_id"):
        v = j.get(k)
        if isinstance(v, str) and v.startswith("0x"):
            return v
    return None


def _job_status(j: Dict[str, Any]) -> str:
    v = (j.get("status") or j.get("state") or j.get("phase") or "").lower()
    return str(v)


def _job_provider_id(j: Dict[str, Any]) -> Optional[str]:
    for k in ("provider_id", "providerId", "provider"):
        v = j.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _claimable(j: Dict[str, Any]) -> bool:
    st = _job_status(j)
    # Heuristic: completed/proved/done/settled but not already paid
    if any(x in st for x in ("completed", "proved", "done", "success", "finished")):
        # avoid already-claimed markers
        if not any(x in st for x in ("paid", "settled")):
            return True
    # Some APIs expose an explicit flag
    for k in ("claimable", "eligible_for_payout", "payout_ready"):
        v = j.get(k)
        if isinstance(v, bool):
            return v
    return False


def _get_job(rpc_url: str, job_id: str) -> Optional[Dict[str, Any]]:
    for m in ("aicf.getJob",):
        try:
            res = _rpc_call(rpc_url, m, [job_id])
            if isinstance(res, dict):
                return res
        except Exception:
            continue
    return None


def _claim_payout(rpc_url: str, job_id: str) -> Any:
    """
    Attempt to claim payout for a job. Accept multiple shapes:
      aicf.claimPayout(job_id) or aicf.claimPayout({"id": job_id})
    """
    try:
        return _rpc_call(rpc_url, "aicf.claimPayout", [job_id])
    except Exception:
        pass
    try:
        return _rpc_call(rpc_url, "aicf.claimPayout", [{"id": job_id}])
    except Exception as exc:
        raise AssertionError(f"claimPayout failed for {job_id}: {exc}") from exc


# -----------------------------------------------------------------------------
# Best-effort enqueue path (if no claimable job is present)
# -----------------------------------------------------------------------------


def _enqueue_ai_job_cli(rpc_url: str) -> Optional[str]:
    """
    Try CLI: python -m capabilities.cli.enqueue_ai --rpc <url> --model tiny --prompt "hi"
    Return task_id hex if printed; else None.
    """
    py = env("ANIMICA_CLI_PYTHON", sys.executable)
    cmd = [
        py,
        "-m",
        "capabilities.cli.enqueue_ai",
        "--rpc",
        rpc_url,
        "--model",
        "tiny",
        "--prompt",
        "ping",
    ]
    try:
        proc = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=60
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        # Try to parse a hex id
        for tok in out.replace("\n", " ").split():
            if isinstance(tok, str) and tok.startswith("0x") and len(tok) >= 10:
                return tok
    except Exception:
        return None
    return None


def _enqueue_ai_job_rpc(rpc_url: str) -> Optional[str]:
    """
    Try cap.enqueue or capabilities.enqueue for AI job.
    """
    req = {"kind": "AI", "model": "tiny", "prompt": "ping"}
    for m in ("cap.enqueueAI", "cap.enqueue", "capabilities.enqueue"):
        try:
            res = _rpc_call(rpc_url, m, [req])
            if isinstance(res, str) and res.startswith("0x"):
                return res
            if isinstance(res, dict):
                v = res.get("task_id") or res.get("id")
                if isinstance(v, str) and v.startswith("0x"):
                    return v
        except Exception:
            continue
    return None


def _ensure_completed_job(rpc_url: str, wait_secs: float) -> Optional[str]:
    """
    Enqueue a small AI job (best-effort) and wait until it appears completed in AICF listJobs/getJob.
    Returns job/task id or None.
    """
    forced = env("ANIMICA_ENQUEUE_VIA")
    task_id: Optional[str] = None
    if forced == "cli":
        task_id = _enqueue_ai_job_cli(rpc_url)
    elif forced == "rpc":
        task_id = _enqueue_ai_job_rpc(rpc_url)
    else:
        task_id = _enqueue_ai_job_cli(rpc_url) or _enqueue_ai_job_rpc(rpc_url)

    if not task_id:
        return None

    deadline = time.time() + wait_secs
    while time.time() < deadline:
        j = _get_job(rpc_url, task_id)
        if isinstance(j, dict):
            if _claimable(j):
                return task_id
        time.sleep(1.0)
    return None


# -----------------------------------------------------------------------------
# The test
# -----------------------------------------------------------------------------


@pytest.mark.timeout(540)
def test_aicf_provider_payout_balance_reflects():
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")

    # Discover providers
    providers = _list_providers(rpc_url)
    if not providers:
        pytest.skip("No AICF providers found — registry may be empty on this devnet.")

    # Choose a provider (first OK)
    prov = None
    for p in providers:
        pid = _provider_id(p)
        if pid:
            prov = (pid, p)
            break
    if not prov:
        pytest.skip("No usable provider id found in AICF providers list.")
    provider_id, provider_obj = prov

    # Baseline balance snapshot
    bal_before = _get_balance(rpc_url, provider_id) or {}
    score_before = _balance_score(bal_before)

    # Try to find a claimable job for this provider; else any provider; else try to enqueue.
    jobs = _list_jobs_any(rpc_url, provider_id=provider_id)
    claimable_ids: List[str] = [
        jid for j in jobs if _claimable(j) and (jid := _job_id(j))
    ]
    job_id: Optional[str] = claimable_ids[0] if claimable_ids else None

    if not job_id:
        # broaden search
        jobs_any = _list_jobs_any(rpc_url, provider_id=None)
        for j in jobs_any:
            if _claimable(j):
                job_id = _job_id(j)
                # Prefer a job tied to our provider, else still try claim (some nets allow it)
                if _job_provider_id(j) in (provider_id, None):
                    break

    if not job_id:
        # Best-effort: enqueue a tiny AI job and wait for completion
        job_id = _ensure_completed_job(
            rpc_url, wait_secs=float(env("ANIMICA_RESULT_WAIT_SECS", "240"))
        )

    if not job_id:
        pytest.skip(
            "No claimable/complete AICF job found (and enqueue path not available)."
        )

    # Attempt to claim payout
    _claim_payout(rpc_url, job_id)

    # Poll for balance change OR job marked settled/paid
    deadline = time.time() + float(env("ANIMICA_SETTLE_WAIT_SECS", "180"))
    last_bal: Dict[str, Any] = bal_before
    while time.time() < deadline:
        # Check job state
        j = _get_job(rpc_url, job_id)
        if isinstance(j, dict):
            st = _job_status(j)
            if any(x in st for x in ("paid", "settled", "credited")):
                # Great: consider this success even if balance lagging a bit
                break
        # Check balance
        bal_now = _get_balance(rpc_url, provider_id) or {}
        last_bal = bal_now
        if _balance_score(bal_now) > score_before:
            break
        time.sleep(1.0)

    # Final assertions: either the score increased OR job claims settled/paid.
    bal_after_score = _balance_score(last_bal)
    j_final = _get_job(rpc_url, job_id) or {}
    settled = any(x in _job_status(j_final) for x in ("paid", "settled", "credited"))

    assert (
        bal_after_score > score_before or settled
    ), f"Payout not reflected: before={bal_before} after={last_bal} job={j_final}"
