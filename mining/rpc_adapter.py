from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

from .share_submitter import AsyncJsonRpcClient, RpcError, TransportError


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
                if isinstance(res, dict) and res.get("jobId"):
                    self._log.debug(
                        "rpc %s ok in %.3fs (jobId=%s)",
                        method,
                        dt,
                        res.get("jobId"),
                    )
                    return res
                self._log.warning(
                    "rpc %s returned no jobId in %.3fs", method, dt
                )
                return None
            except (RpcError, TransportError) as e:
                dt = time.perf_counter() - t0
                self._log.warning(
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
