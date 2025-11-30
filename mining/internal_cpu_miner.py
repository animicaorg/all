from __future__ import annotations

"""
Reference CPU-based Stratum miner used for tests and devnets.

This helper wires ``StratumClient`` to ``HashScanner`` so we can exercise the
Stratum server end-to-end without needing external ASICs or GPUs. It is *not*
an optimized miner; it intentionally keeps the control flow simple and
deterministic so tests can assert on specific nonces.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from .hash_search import HashScanner
from .stratum_client import StratumClient

log = logging.getLogger("mining.cpu_miner")


@dataclass
class CpuMinerResult:
    job_id: str
    nonce: int
    h_micro: int
    accepted: bool
    is_block: bool
    reason: Optional[str]


class CpuStratumMiner:
    """Minimal CPU miner that scans for one share per job and submits it."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 23454,
        agent: str = "animica-cpu-miner/0.1",
        worker: str = "cpu.worker",
        address: str = "anim1qqq",
        scan_window: int = 10_000,
    ) -> None:
        self._client = StratumClient(host, port, agent=agent)
        self._worker = worker
        self._address = address
        self._scanner = HashScanner()
        self._scan_window = scan_window

        self._share_target: float = 0.0
        self._theta_micro: int = 0
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._client.on_notify = self._on_notify
        self._client.on_set_difficulty = self._on_set_difficulty
        await self._client.connect()
        await self._client.subscribe()
        await self._client.authorize(worker=self._worker, address=self._address)

    async def stop(self) -> None:
        self._stop.set()
        await self._client.close()

    async def _on_set_difficulty(self, share_target: float, theta_micro: int) -> None:
        self._share_target = float(share_target)
        self._theta_micro = int(theta_micro)

    async def _on_notify(self, job: dict) -> None:
        if self._stop.is_set():
            return
        header = job.get("header") or {}
        sign_hex = header.get("signBytes")
        if not isinstance(sign_hex, str) or not sign_hex.startswith("0x"):
            log.warning(
                "[cpu-miner] missing signBytes; cannot mine job %s", job.get("jobId")
            )
            return
        prefix = bytes.fromhex(sign_hex[2:])
        theta_micro = self._theta_micro or int(job.get("thetaMicro") or 0)
        share_ratio = float(job.get("shareTarget") or self._share_target or 0.0)
        t_share_micro = max(0, int(theta_micro * share_ratio))

        shares = self._scanner.scan_batch(
            prefix,
            t_share_micro,
            nonce_start=0,
            nonce_count=self._scan_window,
            theta_micro=theta_micro,
        )
        if not shares:
            log.warning(
                "[cpu-miner] no shares found in window for job %s", job.get("jobId")
            )
            return

        share = shares[0]
        hs_body = {"nonce": hex(share.nonce), "body": {"hMicro": share.h_micro}}
        res = await self._client.submit_share(job["jobId"], hs_body)
        log.info(
            "[cpu-miner] submitted nonce=%d accepted=%s",
            share.nonce,
            res.get("accepted"),
        )

    async def run_until_stopped(self) -> None:
        await self.start()
        await self._stop.wait()
        await self.stop()
