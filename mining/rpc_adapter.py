from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

from .share_submitter import AsyncJsonRpcClient, RpcError, TransportError, json_sanitize


@dataclass
class RpcTemplateProvider:
    rpc_url: str
    proof_type: str = "sha256d"
    work_timeout_s: float = 12.0
    connect_timeout_s: float = 2.0
    read_timeout_s: float = 12.0
    write_timeout_s: float = 5.0
    pool_timeout_s: float = 5.0
    max_retries: int = 5
    initial_backoff_s: float = 0.5
    max_backoff_s: float = 5.0
    jitter: float = 0.25
    http_client: Optional[httpx.AsyncClient] = None
    _rpc: AsyncJsonRpcClient = field(init=False)
    _log: logging.Logger = field(
        init=False, default_factory=lambda: logging.getLogger("mining.rpc_adapter")
    )
    _warned_at: Dict[str, float] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        timeout = httpx.Timeout(
            timeout=self.work_timeout_s,
            connect=self.connect_timeout_s,
            read=self.read_timeout_s,
            write=self.write_timeout_s,
            pool=self.pool_timeout_s,
        )
        self._rpc = AsyncJsonRpcClient(
            self.rpc_url,
            {"Content-Type": "application/json"},
            timeout=timeout,
            client=self.http_client,
        )

    def _warn_throttled(self, key: str, message: str, *args: Any) -> None:
        now = time.monotonic()
        last = self._warned_at.get(key, 0.0)
        if now - last >= 10.0:
            self._warned_at[key] = now
            self._log.warning(message, *args)

    def _extract_job_id(self, tpl: Dict[str, Any]) -> Optional[str]:
        for key in (
            "jobId",
            "job_id",
            "jobID",
            "job",
            "id",
            "workId",
            "work_id",
            "templateId",
            "template_id",
        ):
            val = tpl.get(key)
            if isinstance(val, dict):
                nested = val.get("jobId") or val.get("id")
                if nested:
                    return str(nested)
                continue
            if val:
                return str(val)
        job = tpl.get("job")
        if isinstance(job, dict):
            nested = job.get("jobId") or job.get("id")
            if nested:
                return str(nested)
        return None

    def _derive_job_id(self, tpl: Dict[str, Any]) -> str:
        hints = tpl.get("hints") if isinstance(tpl.get("hints"), dict) else {}
        base = {
            "header": tpl.get("header"),
            "signBytes": tpl.get("signBytes") or tpl.get("sign_bytes"),
            "shareTarget": tpl.get("shareTarget"),
            "target": tpl.get("target"),
            "targetBits": tpl.get("targetBits"),
            "height": tpl.get("height") or tpl.get("number"),
            "mixSeed": tpl.get("mixSeed") or hints.get("mixSeed"),
            "proofType": tpl.get("proofType") or tpl.get("proof_type") or self.proof_type,
        }
        payload = json.dumps(
            json_sanitize(base), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"derived-{digest}"

    def _template_is_valid(self, tpl: Dict[str, Any]) -> bool:
        has_header = tpl.get("header") is not None or tpl.get("signBytes") is not None
        has_target = (
            tpl.get("shareTarget") is not None
            or tpl.get("target") is not None
            or tpl.get("targetBits") is not None
        )
        return bool(has_header and has_target)

    async def current_template(self) -> Optional[Dict[str, Any]]:
        method = "miner.getWork"
        backoff = self.initial_backoff_s
        for attempt in range(1, self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                res = await self._rpc.call(
                    method, [{"proofType": self.proof_type}], timeout_s=self.work_timeout_s
                )
                dt = time.perf_counter() - t0
                if os.getenv("ANIMICA_MINER_DEBUG") == "1":
                    self._log.debug("rpc %s response: %s", method, res)
                if not isinstance(res, dict):
                    self._warn_throttled(
                        "invalid-template-shape",
                        "rpc %s returned non-dict result in %.3fs (attempt %s/%s)",
                        method,
                        dt,
                        attempt,
                        self.max_retries,
                    )
                    raise RpcError(-32000, "invalid-template-shape", res)

                tpl = dict(res)
                job_id = self._extract_job_id(tpl)
                if not job_id:
                    job_id = self._derive_job_id(tpl)
                    self._warn_throttled(
                        "derived-job-id",
                        "rpc %s missing jobId; derived=%s in %.3fs (attempt %s/%s)",
                        method,
                        job_id,
                        dt,
                        attempt,
                        self.max_retries,
                    )
                tpl["jobId"] = job_id

                if not self._template_is_valid(tpl):
                    self._warn_throttled(
                        "invalid-template",
                        "rpc %s returned incomplete template in %.3fs (attempt %s/%s)",
                        method,
                        dt,
                        attempt,
                        self.max_retries,
                    )
                    raise RpcError(-32000, "invalid-template", tpl)

                self._log.debug(
                    "rpc %s ok in %.3fs (jobId=%s)", method, dt, tpl.get("jobId")
                )
                return tpl
            except (RpcError, TransportError) as e:
                dt = time.perf_counter() - t0
                key = (
                    f"rpc-error-{getattr(e, 'code', 'transport')}"
                    if isinstance(e, RpcError)
                    else "rpc-transport-error"
                )
                self._warn_throttled(
                    key,
                    "rpc %s failed (attempt %s/%s) after %.3fs: %s",
                    method,
                    attempt,
                    self.max_retries,
                    dt,
                    e,
                )
                if attempt >= self.max_retries:
                    return None
                sleep = backoff * (1.0 + (random.random() * 2 - 1) * self.jitter)
                sleep = max(0.0, min(sleep, self.max_backoff_s))
                await asyncio.sleep(sleep)
                backoff = min(backoff * 2.0, self.max_backoff_s)
        return None

    async def aclose(self) -> None:
        await self._rpc.aclose()
