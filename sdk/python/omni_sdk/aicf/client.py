"""
omni_sdk.aicf.client
====================

High-level client for the AI Compute Fund (AICF) & Capabilities job APIs.

This client primarily talks to JSON-RPC methods mounted by the node:

Capabilities (read/write where safe):
- cap.enqueueAI            → { jobId }
- cap.enqueueQuantum       → { jobId }
- cap.getJob               → JobRecord
- cap.listJobs             → [JobRecord]
- cap.getResult            → ResultRecord | { ok: False, ... }

AICF (provider registry & balances; mostly read-only):
- aicf.listProviders       → [Provider]
- aicf.getProvider         → Provider
- aicf.getBalance          → { balance }
- aicf.claimPayout         → { txHash }  (may require node-side auth/permissions)

The client also provides polling helpers to wait for job completion.

Example
-------
    from omni_sdk.rpc.http import HttpClient
    from omni_sdk.aicf.client import AICFClient

    rpc = HttpClient("http://127.0.0.1:8545")
    aicf = AICFClient(rpc)

    job_id = aicf.enqueue_ai(model="tiny", prompt=b"hello", fee=1234)
    result = aicf.wait_result(job_id, timeout_s=30.0)

Design notes
------------
* All byte payloads are hex-encoded (0x…) for JSON-RPC.
* The client accepts either an RPC object exposing `.call(method, params)`
  or a base URL string; in the latter case it falls back to direct JSON-RPC
  POSTs to `<base>/rpc`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple, Union)
from urllib.parse import urljoin

try:
    import requests
except Exception as _e:  # pragma: no cover
    raise RuntimeError("requests is required for omni_sdk.aicf.client") from _e

# Bytes/hex helpers
try:
    from omni_sdk.utils.bytes import to_hex as _to_hex  # type: ignore
except Exception:

    def _to_hex(b: bytes) -> str:  # pragma: no cover
        return "0x" + bytes(b).hex()


# Error surface
try:
    from omni_sdk.errors import RpcError  # type: ignore
except Exception:  # pragma: no cover

    class RpcError(RuntimeError): ...


JsonDict = Dict[str, Any]


def _detect_base_url(rpc_or_url: Union[str, Any]) -> Optional[str]:
    if isinstance(rpc_or_url, str):
        return rpc_or_url
    for attr in ("base_url", "endpoint", "url"):
        v = getattr(rpc_or_url, attr, None)
        if isinstance(v, str) and v:
            return v
    return None


@dataclass(frozen=True)
class _RPCCompat:
    """Tiny adapter to support either a rich RPC client or direct HTTP JSON-RPC."""

    call_fn: Any
    base_url: Optional[str]
    session: requests.Session


def _wrap_rpc(
    rpc_or_url: Union[str, Any],
    *,
    session: Optional[requests.Session],
    timeout_s: float,
) -> _RPCCompat:
    # If object exposes .call(method, params), prefer it.
    if not isinstance(rpc_or_url, str) and hasattr(rpc_or_url, "call"):
        call_fn = getattr(rpc_or_url, "call")
        base = _detect_base_url(rpc_or_url)
        return _RPCCompat(
            call_fn=call_fn, base_url=base, session=session or requests.Session()
        )

    # Otherwise, we build a direct JSON-RPC shim
    base = (
        _detect_base_url(rpc_or_url) if not isinstance(rpc_or_url, str) else rpc_or_url
    )
    if not isinstance(base, str) or not base:
        raise ValueError(
            "AICFClient needs an RPC object with .call(...) or a base URL string"
        )

    rpc_url = urljoin(base.rstrip("/") + "/", "rpc")
    sess = session or requests.Session()

    def _direct_call(method: str, params: Any = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        try:
            resp = sess.post(rpc_url, json=payload, timeout=timeout_s)
        except requests.RequestException as e:  # pragma: no cover
            raise RpcError(f"POST {rpc_url} failed: {e}") from e
        try:
            data = resp.json()
        except Exception as e:
            raise RpcError(
                f"Invalid JSON-RPC response from {rpc_url}: {resp.text[:256]}"
            ) from e
        if "error" in data and data["error"]:
            err = data["error"]
            raise RpcError(f"RPC {method} failed: {err}")
        return data.get("result")

    return _RPCCompat(call_fn=_direct_call, base_url=base, session=sess)


class AICFClient:
    """
    Client for enqueuing AI/Quantum jobs (Capabilities API) and reading AICF registry state.

    Parameters
    ----------
    rpc_or_url : str | RPC client
        RPC client with `.call(method, params)` or a base URL string.
    timeout_s : float
        Default network timeout.
    session : requests.Session | None
        Optional custom requests session (used for direct JSON-RPC mode).
    """

    def __init__(
        self,
        rpc_or_url: Union[str, Any],
        *,
        timeout_s: float = 30.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._timeout = float(timeout_s)
        self._rpc = _wrap_rpc(rpc_or_url, session=session, timeout_s=self._timeout)

    # -------------------------------------------------------------------------
    # Enqueue helpers
    # -------------------------------------------------------------------------

    def enqueue_ai(
        self,
        *,
        model: str,
        prompt: Union[bytes, bytearray, memoryview, str],
        fee: int = 0,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """
        Enqueue an AI job via `cap.enqueueAI`.

        Returns
        -------
        job_id : str (hex)
        """
        if isinstance(prompt, str):
            prompt_bytes = prompt.encode("utf-8")
        else:
            prompt_bytes = bytes(prompt)

        params: JsonDict = {
            "model": str(model),
            "prompt": _to_hex(prompt_bytes),
            "fee": int(fee),
        }
        if max_tokens is not None:
            params["maxTokens"] = int(max_tokens)
        if temperature is not None:
            params["temperature"] = float(temperature)
        if metadata:
            params["metadata"] = dict(metadata)

        # Prefer capabilities API
        try:
            res = self._rpc.call_fn("cap.enqueueAI", params)
        except Exception as e:
            # Attempt optional AICF compatibility method if exposed
            try:
                res = self._rpc.call_fn("aicf.queueSubmitAI", params)  # type: ignore
            except Exception as e2:
                raise RpcError(
                    f"enqueue_ai failed via cap.enqueueAI and aicf.queueSubmitAI: {e} / {e2}"
                ) from e2

        job_id = (
            res.get("jobId")
            if isinstance(res, Mapping)
            else (res if isinstance(res, str) else None)
        )
        if not isinstance(job_id, str):
            raise RpcError("enqueue_ai: server did not return a jobId")
        return job_id

    def enqueue_quantum(
        self,
        *,
        circuit: Union[Mapping[str, Any], Sequence[Any], bytes, str],
        shots: int = 100,
        fee: int = 0,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """
        Enqueue a Quantum job via `cap.enqueueQuantum`.

        Parameters
        ----------
        circuit : dict | list | str | bytes
            Circuit description; dict/list will be JSON-serialized, str taken as JSON,
            bytes are sent raw (hex-encoded).
        shots : int
            Number of measurement shots.
        fee : int
            Offered fee units for the job.

        Returns
        -------
        job_id : str (hex)
        """
        payload_hex: str
        if isinstance(circuit, (dict, list, tuple)):
            payload_hex = _to_hex(
                json.dumps(circuit, separators=(",", ":")).encode("utf-8")
            )
        elif isinstance(circuit, str):
            # If it looks like JSON, pass as-is; always hex-encode bytes on the wire
            payload_hex = _to_hex(circuit.encode("utf-8"))
        else:
            payload_hex = _to_hex(bytes(circuit))

        params: JsonDict = {
            "circuit": payload_hex,
            "shots": int(shots),
            "fee": int(fee),
        }
        if metadata:
            params["metadata"] = dict(metadata)

        try:
            res = self._rpc.call_fn("cap.enqueueQuantum", params)
        except Exception as e:
            try:
                res = self._rpc.call_fn("aicf.queueSubmitQuantum", params)  # type: ignore
            except Exception as e2:
                raise RpcError(
                    f"enqueue_quantum failed via cap.enqueueQuantum and aicf.queueSubmitQuantum: {e} / {e2}"
                ) from e2

        job_id = (
            res.get("jobId")
            if isinstance(res, Mapping)
            else (res if isinstance(res, str) else None)
        )
        if not isinstance(job_id, str):
            raise RpcError("enqueue_quantum: server did not return a jobId")
        return job_id

    # -------------------------------------------------------------------------
    # Job & result inspection
    # -------------------------------------------------------------------------

    def get_job(self, job_id: str) -> JsonDict:
        """Return the server's JobRecord for `job_id`."""
        res = self._rpc.call_fn("cap.getJob", {"jobId": job_id})
        if not isinstance(res, Mapping):
            raise RpcError("get_job: invalid response type")
        return dict(res)

    def list_jobs(
        self,
        *,
        kind: Optional[str] = None,  # "AI" | "Quantum"
        status: Optional[str] = None,  # "Queued" | "Assigned" | "Completed" | "Expired"
        limit: int = 50,
        cursor: Optional[str] = None,
        caller: Optional[str] = None,  # filter by contract/account address (if indexed)
    ) -> List[JsonDict]:
        """List recent jobs with optional filters (if supported by the server)."""
        params: JsonDict = {"limit": int(limit)}
        if kind:
            params["kind"] = str(kind)
        if status:
            params["status"] = str(status)
        if cursor:
            params["cursor"] = str(cursor)
        if caller:
            params["caller"] = str(caller)
        res = self._rpc.call_fn("cap.listJobs", params)
        if not isinstance(res, list):
            raise RpcError("list_jobs: invalid response type")
        return [dict(x) for x in res if isinstance(x, Mapping)]

    def get_result(self, job_id: str) -> JsonDict:
        """
        Return the result record for `job_id` if available.

        If the result is not yet ready, servers commonly return a dict like
        {"ok": False, "reason": "pending"} or raise an RPC error. We surface
        either shape as an exception.
        """
        res = self._rpc.call_fn("cap.getResult", {"jobId": job_id})
        if not isinstance(res, Mapping):
            raise RpcError("get_result: invalid response type")
        # If server signals not ready
        if res.get("ok") is False and "result" not in res:
            reason = res.get("reason") or "not ready"
            raise RpcError(f"result not ready for job {job_id}: {reason}")
        return dict(res)

    def wait_result(
        self,
        job_id: str,
        *,
        timeout_s: float = 120.0,
        poll_interval_s: float = 0.5,
        on_progress: Optional[Any] = None,
    ) -> JsonDict:
        """
        Poll `cap.getResult` until a result is available or timeout expires.

        Parameters
        ----------
        job_id : str
        timeout_s : float
        poll_interval_s : float
        on_progress : Optional[callable(job_dict)] called between polls (best effort)

        Returns
        -------
        ResultRecord dict
        """
        deadline = time.monotonic() + float(timeout_s)
        last_status: Optional[str] = None
        while True:
            try:
                res = self.get_result(job_id)
                return res
            except RpcError:
                # Not ready. Optionally surface job status to progress hook.
                try:
                    job = self.get_job(job_id)
                    status = str(job.get("status", ""))
                    if on_progress and status != last_status:
                        on_progress(job)
                    last_status = status
                except Exception:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for result of job {job_id}")
                time.sleep(float(poll_interval_s))

    # -------------------------------------------------------------------------
    # Provider registry & balances (AICF)
    # -------------------------------------------------------------------------

    def list_providers(
        self,
        *,
        capability: Optional[str] = None,  # "AI" | "Quantum"
        region: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> List[JsonDict]:
        """List registered providers with optional filters."""
        params: JsonDict = {"limit": int(limit)}
        if capability:
            params["capability"] = str(capability)
        if region:
            params["region"] = str(region)
        if cursor:
            params["cursor"] = str(cursor)
        res = self._rpc.call_fn("aicf.listProviders", params)
        if not isinstance(res, list):
            raise RpcError("list_providers: invalid response type")
        return [dict(x) for x in res if isinstance(x, Mapping)]

    def get_provider(self, provider_id: Union[str, int]) -> JsonDict:
        """Fetch a single provider record by id."""
        res = self._rpc.call_fn("aicf.getProvider", {"id": provider_id})
        if not isinstance(res, Mapping):
            raise RpcError("get_provider: invalid response type")
        return dict(res)

    def get_balance(self, account: str) -> int:
        """Return AICF balance for an account or provider address."""
        res = self._rpc.call_fn("aicf.getBalance", {"account": account})
        if isinstance(res, Mapping) and "balance" in res:
            res = res["balance"]
        if not isinstance(res, int):
            raise RpcError("get_balance: invalid response")
        return int(res)

    def claim_payout(self, *, provider: str, amount: Optional[int] = None) -> JsonDict:
        """
        Initiate a payout claim (if the node/service permits via RPC).

        Note: In many deployments, payouts are settled automatically or require
        on-chain transactions initiated by providers. This method is best-effort.
        """
        params: JsonDict = {"provider": provider}
        if amount is not None:
            params["amount"] = int(amount)
        res = self._rpc.call_fn("aicf.claimPayout", params)
        if not isinstance(res, Mapping):
            raise RpcError("claim_payout: invalid response")
        return dict(res)


__all__ = ["AICFClient"]
