"""
animica.ai.market — thin client for the live AICF job/provider RPC protocol.

Wraps the node's ``aicf.*`` JSON-RPC methods (implemented in
``rpc/methods/aicf_jobs.py``) so the ``animica ai job`` / ``ai provider`` /
``ai earnings`` commands talk to the REAL marketplace — estimate, submit,
status, settle, worker register/status/earnings — with no fabricated data.

Spending safety (5.2.0 §9): the only method that moves ANM is ``submit_job``,
and it requires a caller-built, caller-signed payment envelope
(``{"txn_hex": ...}``). This module never builds or signs a payment itself —
the CLI does that explicitly after a quote + confirmation, so nothing here can
spend by surprise.

There is no per-user "list jobs" RPC, so the CLI tracks submitted job ids in a
small local file (see ``local_jobs``); these helpers are pure RPC.
"""

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Optional

from ..cli.paths import ensure_file_dir, get_animica_home

DEFAULT_RPC = "http://127.0.0.1:8545/rpc"


class MarketError(RuntimeError):
    """An aicf.* RPC call failed or the node was unreachable."""


def rpc_url() -> str:
    return (os.environ.get("ANIMICA_RPC_URL") or "").strip() or DEFAULT_RPC


def call(method: str, params: Optional[dict] = None, *, timeout: float = 20.0,
         url: Optional[str] = None) -> Any:
    """Call a node JSON-RPC method. Raises MarketError on transport/RPC error."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(url or rpc_url(), data=body,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            out = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise MarketError(f"node RPC unreachable at {url or rpc_url()}: {e}") from e
    if isinstance(out, dict) and out.get("error"):
        raise MarketError(f"{method}: {out['error']}")
    return out.get("result") if isinstance(out, dict) else out


# --- consumer side --------------------------------------------------------- #
def estimate(prompt_tokens: int, max_output_tokens: int = 256,
             tier: Optional[str] = None, **kw) -> dict:
    return call("aicf.estimateJobCost", {
        "prompt_tokens": int(prompt_tokens),
        "max_output_tokens": int(max_output_tokens),
        "tier_preferred": tier,
    }, **kw)


def treasury_address(**kw) -> str:
    res = call("aicf.getTreasuryAddress", {}, **kw)
    if isinstance(res, dict):
        return str(res.get("address") or res.get("treasury") or "")
    return str(res or "")


def submit_job(spec: dict, payment: dict, **kw) -> dict:
    """Submit a job. ``payment`` must be a caller-signed envelope ({'txn_hex': ...})."""
    return call("aicf.submitInferenceJob", {"spec": spec, "payment": payment}, **kw)


def job_status(job_id: str, **kw) -> dict:
    return call("aicf.jobStatus", {"job_id": job_id}, **kw)


def settle_job(job_id: str, **kw) -> dict:
    return call("aicf.settleJob", {"job_id": job_id}, **kw)


# --- provider side --------------------------------------------------------- #
def register_worker(address: str, tiers: Optional[list] = None,
                    hardware: Optional[dict] = None,
                    direct_endpoint: Optional[str] = None, **kw) -> dict:
    params: dict[str, Any] = {"address": address, "tiers": tiers or []}
    if hardware is not None:
        params["hardware"] = hardware
    if direct_endpoint:
        params["direct_endpoint"] = direct_endpoint
    return call("aicf.workerRegister", params, **kw)


def worker_status(address: str, **kw) -> dict:
    return call("aicf.workerStatus", {"address": address}, **kw)


def worker_earnings(address: str, **kw) -> dict:
    return call("aicf.workerEarnings", {"address": address}, **kw)


# --- local job tracking (no per-user list RPC exists) ---------------------- #
def _jobs_path() -> Path:
    return get_animica_home() / "ai_jobs.json"


def local_jobs() -> list[dict]:
    try:
        return json.loads(_jobs_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []


def record_job(entry: dict) -> None:
    """Append a submitted job record (job_id, tier, cost, created, tx) locally."""
    jobs = local_jobs()
    jobs.append(entry)
    p = _jobs_path()
    ensure_file_dir(p)
    p.write_text(json.dumps(jobs[-500:], indent=2), encoding="utf-8")  # cap growth
