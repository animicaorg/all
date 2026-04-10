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

from .hash_search import HashScanner, h_micro_from_digest, micro_threshold_to_target256
from .stratum_client import StratumClient
from .template_block import hash_candidate_header, header_from_template_view

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

    @staticmethod
    def _header_template_from_job(job: dict) -> Optional[dict]:
        header = job.get("header") or {}
        if not isinstance(header, dict):
            return None
        required = (
            "parentHash",
            "stateRoot",
            "txsRoot",
            "receiptsRoot",
            "proofsRoot",
            "daRoot",
            "mixSeed",
            "poiesPolicyRoot",
            "pqAlgPolicyRoot",
            "timestamp",
        )
        if not all(key in header for key in required):
            return None
        return header

    def _scan_header_template(
        self,
        header_view: dict,
        *,
        theta_micro: int,
        share_ratio: float,
    ) -> Optional[CpuMinerResult]:
        target_int = micro_threshold_to_target256(
            max(1, int(theta_micro * max(share_ratio, 1e-9)))
        )
        header = header_from_template_view(header_view, nonce=0)
        for nonce in range(self._scan_window):
            candidate_hash = hash_candidate_header(header, nonce=nonce)
            if candidate_hash.digest_int > target_int:
                continue
            h_micro = h_micro_from_digest(candidate_hash.digest)
            return CpuMinerResult(
                job_id=str(header_view.get("hash") or "unknown"),
                nonce=nonce,
                h_micro=h_micro,
                accepted=False,
                is_block=False,
                reason=None,
            )
        return None

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
        theta_micro = self._theta_micro or int(
            job.get("thetaMicro")
            or job.get("thetaTargetMicro")
            or job.get("theta_micro")
            or 0
        )
        if theta_micro <= 0:
            log.warning(
                "[cpu-miner] missing thetaMicro; cannot mine job %s", job.get("jobId")
            )
            return
        share_ratio = float(job.get("shareTarget") or self._share_target or 0.0)
        if share_ratio <= 0.0:
            share_ratio = 1.0
        share = None
        header_template = self._header_template_from_job(job)
        if header_template is not None:
            share = self._scan_header_template(
                header_template,
                theta_micro=theta_micro,
                share_ratio=share_ratio,
            )
        else:
            sign_hex = header.get("signBytes")
            if not isinstance(sign_hex, str) or not sign_hex.startswith("0x"):
                log.warning(
                    "[cpu-miner] missing usable header template; cannot mine job %s",
                    job.get("jobId"),
                )
                return
            prefix = bytes.fromhex(sign_hex[2:])
            t_share_micro = max(1, int(theta_micro * share_ratio))
            shares = self._scanner.scan_batch(
                prefix,
                t_share_micro,
                nonce_start=0,
                nonce_count=self._scan_window,
                theta_micro=theta_micro,
            )
            if shares:
                found = shares[0]
                share = CpuMinerResult(
                    job_id=str(job.get("jobId") or "unknown"),
                    nonce=found.nonce,
                    h_micro=found.h_micro,
                    accepted=False,
                    is_block=False,
                    reason=None,
                )

        if share is None:
            log.warning(
                "[cpu-miner] no shares found in window for job %s", job.get("jobId")
            )
            return

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
