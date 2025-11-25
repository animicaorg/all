"""
capabilities.adapters.aicf
=========================

Facade between the *capabilities* subsystem and the **AICF** (AI Compute Fund)
job queue / scheduler.

It provides a minimal, dependency-light API:

- enqueue_ai(model, prompt, **opts) -> {"job_id": "...", "receipt": {...}}
- enqueue_quantum(circuit, shots, **opts) -> {"job_id": "...", "receipt": {...}}
- get_job(job_id) -> {"job_id": "...", "status": "...", "result": {...}, ...}
- wait_for_completion(job_id, timeout_s=60.0, poll_interval_s=0.5) -> job dict

Resolution order for backends:
1) An explicitly supplied *service* object with suitable methods (duck-typed).
2) JSON-RPC endpoint (env AICF_ENDPOINT or provided `endpoint=...`).
3) If neither is available, a clear RuntimeError is raised.

This module deliberately avoids importing the entire `aicf.*` tree at import
time. When possible, calls are duck-typed to keep this adapter usable even
in minimal deployments (e.g., tests).
"""

from __future__ import annotations

import json
import os
import time
import base64
import binascii
import random
import string
from typing import Any, Dict, Optional, Union, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

HexLike = Union[str, bytes, bytearray, memoryview]

__all__ = [
    "enqueue_ai",
    "enqueue_quantum",
    "get_job",
    "wait_for_completion",
    "AICFAdapter",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand_id(k: int = 8) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(k))

def _as_hex(data: Union[bytes, bytearray, memoryview, str]) -> str:
    if isinstance(data, (bytes, bytearray, memoryview)):
        return "0x" + bytes(data).hex()
    s = str(data)
    return s if s.startswith("0x") else "0x" + s.encode().hex()  # text → utf-8 bytes → hex

def _from_hex(x: HexLike) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    s = str(x).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:
        raise ValueError(f"Invalid hex: {x}") from e

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")

_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "EXPIRED", "CANCELLED"}

# ---------------------------------------------------------------------------
# JSON-RPC shim (stdlib only)
# ---------------------------------------------------------------------------

class _JsonRpc:
    def __init__(self, url: str, api_key: Optional[str] = None, timeout: float = 30.0):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    def call(self, method: str, params: Any) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": _rand_id(),
            "method": method,
            "params": params,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except HTTPError as e:
            raise RuntimeError(f"HTTP {e.code} calling {method}: {e.read().decode('utf-8', 'ignore')}") from e
        except URLError as e:
            raise RuntimeError(f"Network error calling {method}: {e}") from e
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"Non-JSON response from RPC: {raw[:256]!r}") from e
        if "error" in obj:
            code = obj["error"].get("code")
            msg = obj["error"].get("message")
            raise RuntimeError(f"RPC error {code} for {method}: {msg}")
        return obj.get("result")

# ---------------------------------------------------------------------------
# Core adapter
# ---------------------------------------------------------------------------

class AICFAdapter:
    """
    A thin facade that can route to:
      - a provided *service* object which exposes one of:
          enqueue_ai(...), enqueue_quantum(...), get_job(job_id)
        OR a generic submit_job(kind=..., payload=..., **opts)
      - a JSON-RPC endpoint exposing:
          aicf.queueSubmit, aicf.getJob  (best-effort; method names may vary)
    """

    def __init__(
        self,
        *,
        service: Optional[Any] = None,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: float = 30.0,
    ):
        self.service = service
        self.endpoint = endpoint or os.getenv("AICF_ENDPOINT")
        self.api_key = api_key or os.getenv("AICF_API_KEY")
        self.timeout_s = timeout_s
        self.rpc = _JsonRpc(self.endpoint, self.api_key, timeout_s) if self.endpoint else None

    # -------- Submission --------

    def enqueue_ai(
        self,
        *,
        model: str,
        prompt: Union[str, bytes, bytearray, memoryview],
        fee_units: Optional[int] = None,
        tags: Optional[Dict[str, Any]] = None,
        priority: Optional[float] = None,
        ttl_s: Optional[float] = None,
        caller: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "model": model,
            # For JSON-RPC we send both hex and base64; servers can pick.
            "prompt_hex": _as_hex(prompt),
            "prompt_b64": _b64(prompt if isinstance(prompt, (bytes, bytearray, memoryview)) else str(prompt).encode()),
            "fee_units": fee_units,
            "tags": tags or {},
            "priority": priority,
            "ttl_s": ttl_s,
            "caller": caller,
        }
        return self._submit(kind="AI", payload=payload)

    def enqueue_quantum(
        self,
        *,
        circuit: Dict[str, Any],
        shots: int,
        fee_units: Optional[int] = None,
        tags: Optional[Dict[str, Any]] = None,
        priority: Optional[float] = None,
        ttl_s: Optional[float] = None,
        caller: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "circuit": circuit,
            "shots": int(shots),
            "fee_units": fee_units,
            "tags": tags or {},
            "priority": priority,
            "ttl_s": ttl_s,
            "caller": caller,
        }
        return self._submit(kind="QUANTUM", payload=payload)

    def _submit(self, *, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 1) Service duck-typing
        if self.service is not None:
            # Prefer specific methods:
            if kind == "AI":
                meth = getattr(self.service, "enqueue_ai", None)
                if callable(meth):
                    rec = meth(**payload)  # type: ignore[misc]
                    return _normalize_submit_receipt(rec)
            if kind == "QUANTUM":
                meth = getattr(self.service, "enqueue_quantum", None)
                if callable(meth):
                    rec = meth(**payload)  # type: ignore[misc]
                    return _normalize_submit_receipt(rec)
            # Fallback: generic submit
            submit = getattr(self.service, "submit_job", None)
            if callable(submit):
                rec = submit(kind=kind, payload=payload)  # type: ignore[misc]
                return _normalize_submit_receipt(rec)
            raise RuntimeError(
                "Provided service does not implement enqueue_ai/enqueue_quantum/submit_job"
            )

        # 2) JSON-RPC
        if self.rpc:
            params = {"kind": kind, "payload": payload}
            # Try a few likely method names, in order:
            for method in ("aicf.queueSubmit", "aicf.enqueue", "aicf.submitJob"):
                try:
                    res = self.rpc.call(method, params)
                    return _normalize_submit_receipt(res)
                except RuntimeError as e:
                    # Keep trying if method not found; else rethrow
                    if "Method not found" in str(e) or "RPC error -32601" in str(e):
                        continue
                    raise
            raise RuntimeError("No supported AICF RPC submission method found")

        raise RuntimeError("No AICF backend configured (service or endpoint required)")

    # -------- Querying --------

    def get_job(self, job_id: str) -> Dict[str, Any]:
        # 1) Service duck-typing
        if self.service is not None:
            for name in ("get_job", "fetch_job", "job_status"):
                fn = getattr(self.service, name, None)
                if callable(fn):
                    job = fn(job_id)  # type: ignore[misc]
                    return _normalize_job(job)
            raise RuntimeError("Provided service lacks get_job/fetch_job/job_status")

        # 2) JSON-RPC
        if self.rpc:
            # Try a couple names
            for method in ("aicf.getJob", "aicf.jobGet", "aicf.jobStatus"):
                try:
                    res = self.rpc.call(method, {"job_id": job_id})
                    return _normalize_job(res)
                except RuntimeError as e:
                    if "Method not found" in str(e) or "RPC error -32601" in str(e):
                        continue
                    raise
            raise RuntimeError("No supported AICF RPC getJob method found")

        raise RuntimeError("No AICF backend configured (service or endpoint required)")

    def wait_for_completion(
        self,
        job_id: str,
        *,
        timeout_s: float = 60.0,
        poll_interval_s: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Poll until the job reaches a terminal status or timeout elapses.
        Returns the final job object.
        """
        deadline = time.monotonic() + timeout_s
        last = None
        while True:
            last = self.get_job(job_id)
            status = str(last.get("status", "")).upper()
            if status in _TERMINAL_STATUSES:
                return last
            if time.monotonic() >= deadline:
                raise TimeoutError(f"AICF job {job_id} not completed in {timeout_s}s (last status={status})")
            time.sleep(poll_interval_s)

# ---------------------------------------------------------------------------
# Public module-level convenience functions
# ---------------------------------------------------------------------------

# A process-global default adapter which picks up env if the user doesn't pass one.
_default_adapter: Optional[AICFAdapter] = None

def _get_default() -> AICFAdapter:
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = AICFAdapter()
    return _default_adapter

def enqueue_ai(**kwargs) -> Dict[str, Any]:
    return _get_default().enqueue_ai(**kwargs)

def enqueue_quantum(**kwargs) -> Dict[str, Any]:
    return _get_default().enqueue_quantum(**kwargs)

def get_job(job_id: str) -> Dict[str, Any]:
    return _get_default().get_job(job_id)

def wait_for_completion(job_id: str, **kwargs) -> Dict[str, Any]:
    return _get_default().wait_for_completion(job_id, **kwargs)

# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def _normalize_submit_receipt(obj: Any) -> Dict[str, Any]:
    """
    Accepts various shapes from service/RPC and returns:
      {"job_id": str, "receipt": dict}
    """
    if isinstance(obj, dict):
        # Common fields
        job_id = obj.get("job_id") or obj.get("id") or obj.get("jobId")
        if not job_id:
            # Sometimes receipts are nested
            job_id = (obj.get("receipt") or {}).get("job_id") or (obj.get("result") or {}).get("job_id")
        rec = obj.get("receipt") or obj.get("result") or obj
        if not isinstance(rec, dict):
            rec = {"raw": rec}
        if not job_id:
            # As a last resort, derive a synthetic id from fields (NOT consensus)
            job_id = "local-" + _rand_id(10)
        return {"job_id": str(job_id), "receipt": rec}

    # Fallback: stringify
    return {"job_id": "local-" + _rand_id(10), "receipt": {"raw": obj}}

def _normalize_job(obj: Any) -> Dict[str, Any]:
    """
    Normalize job info. Returns at minimum:
      {"job_id": str, "status": str, "result": <maybe None>, ...}
    """
    if not isinstance(obj, dict):
        return {"job_id": "unknown", "status": "UNKNOWN", "raw": obj}

    job_id = obj.get("job_id") or obj.get("id") or obj.get("jobId") or "unknown"
    status = (obj.get("status") or obj.get("state") or "UNKNOWN")
    # Result may appear under different keys
    result = obj.get("result") or obj.get("output") or obj.get("payload") or None
    out = dict(obj)
    out["job_id"] = str(job_id)
    out["status"] = str(status)
    out["result"] = result
    return out
