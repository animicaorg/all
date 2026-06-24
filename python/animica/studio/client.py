"""Thin JSON-RPC client for the node's ``aicf.*`` broker + ``aicf.fn.*`` registry.

This is the only place that knows the wire names of the live compute rails. It
speaks plain JSON-RPC 2.0 to ``POST {rpc_url}/rpc`` and is deliberately small —
the broker (job lifecycle) and registry (deployed functions) already exist; we
just call them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from .errors import JobError


class BrokerClient:
    def __init__(self, rpc_url: str, *, timeout: float = 30.0):
        # Accept both ".../" and ".../rpc"; normalize to the /rpc endpoint.
        base = rpc_url.rstrip("/")
        self.endpoint = base if base.endswith("/rpc") else base + "/rpc"
        self._http = httpx.Client(timeout=timeout)
        self._id = 0

    def close(self) -> None:
        self._http.close()

    def _rpc(self, method: str, params: Any = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        resp = self._http.post(self.endpoint, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body and body["error"]:
            err = body["error"]
            raise JobError(f"{method} failed: {err.get('message', err)} (code {err.get('code')})")
        return body.get("result")

    def reachable(self) -> bool:
        try:
            self._rpc("chain.getChainId")
            return True
        except Exception:
            return False

    # ---- chain / state ----------------------------------------------------

    def chain_id(self) -> int:
        return int(self._rpc("chain.getChainId"))

    def get_nonce(self, address: str) -> int:
        return int(self._rpc("state.getNonce", [address]))

    def get_balance(self, address: str) -> int:
        return int(self._rpc("state.getBalance", [address]))

    def send_raw_tx(self, raw_hex: str) -> str:
        res = self._rpc("tx.sendRawTransaction", [raw_hex])
        return res["tx_hash"] if isinstance(res, dict) else res

    # ---- AICF broker (job lifecycle) -------------------------------------

    def treasury_address(self) -> str:
        try:
            return self._rpc("aicf.getTreasuryAddress")
        except Exception:
            return ""

    def estimate_cost(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        return self._rpc("aicf.estimateJobCost", spec)

    def submit_job(self, spec: Dict[str, Any], payment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._rpc("aicf.submitInferenceJob", {"spec": spec, "payment": payment or {}})

    def settle_job(self, job_id: str) -> Dict[str, Any]:
        return self._rpc("aicf.settleJob", {"job_id": job_id})

    # ---- AICF worker side (fleet provider) -------------------------------

    def worker_register(self, address: str, tiers, *, hardware=None, capabilities=None) -> dict:
        return self._rpc("aicf.workerRegister", {
            "address": address, "tiers": tiers,
            "hardware": hardware or {}, "capabilities": capabilities or [],
        })

    def worker_claim(self, address: str, tiers) -> dict:
        res = self._rpc("aicf.workerClaimNextJob", {"address": address, "tiers": tiers})
        return res or {}

    def worker_submit_result(self, address: str, job_id: str, text: str, *, usage=None) -> dict:
        return self._rpc("aicf.workerSubmitResult", {
            "address": address, "job_id": job_id, "text": text, "usage": usage or {},
        })

    def worker_submit_failure(self, address: str, job_id: str, reason: str) -> dict:
        return self._rpc("aicf.workerFailJob", {
            "address": address, "job_id": job_id, "reason": reason,
        })

    # ---- aicf.fn.* registry (deployed functions) -------------------------

    def fn_deploy(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        return self._rpc("aicf.fn.deploy", meta)

    def fn_get(self, name: str) -> Dict[str, Any]:
        return self._rpc("aicf.fn.get", {"name": name})

    def fn_list(self) -> List[Dict[str, Any]]:
        res = self._rpc("aicf.fn.list")
        return res.get("items", res) if isinstance(res, dict) else res
