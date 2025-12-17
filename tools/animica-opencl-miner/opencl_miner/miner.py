from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import Any, Dict, Optional

from .job import JobConfig
from .scanner import OpenCLScanner, ShareCandidate
from .stratum_client import StratumClient

log = logging.getLogger("opencl_miner.core")


def _decode_hex(value: Optional[str], *, fallback_bytes: int = 0) -> bytes:
    if isinstance(value, str):
        data = value[2:] if value.startswith("0x") else value
        try:
            return bytes.fromhex(data)
        except ValueError:
            return b"\x00" * fallback_bytes
    return b"\x00" * fallback_bytes


class StratumOpenCLMiner:
    """
    High-level coordinator that connects to a Stratum server and feeds jobs to
    the OpenCL worker thread.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 23454,
        worker: str = "opencl.worker",
        address: str,
        iterations: int = 50_000_000,
        max_found: int = 4,
        device_kwargs: Optional[Dict[str, Any]] = None,
        agent: str = "animica-opencl/0.1",
    ) -> None:
        self._host = host
        self._port = port
        self._agent = agent
        self._client: Optional[StratumClient] = None
        self._worker = worker
        self._address = address
        self._iterations = iterations
        self._max_found = max_found
        self._device_kwargs = device_kwargs or {}

        self._current_share_target = 0.01
        self._current_theta = 800_000
        self._active_job: Optional[JobConfig] = None
        self._scanner: Optional[OpenCLScanner] = None
        self._share_queue: "asyncio.Queue[ShareCandidate]" = asyncio.Queue()
        self._submit_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._scanner = OpenCLScanner(
            self._share_queue,
            loop=loop,
            iterations=self._iterations,
            max_found=self._max_found,
            device_kwargs=self._device_kwargs,
        )
        self._scanner.start()
        self._scanner.wait_ready()
        try:
            device_info = getattr(self._scanner, "device_info", None)
            if device_info:
                log.info("GPU initialized: %s", device_info)
        except Exception:
            pass

        await self._connect_with_retries()

        self._submit_task = asyncio.create_task(self._share_consumer())

    async def stop(self) -> None:
        self._stop.set()
        if self._submit_task:
            self._submit_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._submit_task
        if self._client:
            await self._client.close()
        if self._scanner:
            self._scanner.stop()
            self._scanner.join(timeout=2.0)

    async def wait_forever(self) -> None:
        await self._stop.wait()

    # ------------------- Stratum callbacks -------------------

    async def _handle_set_difficulty(self, share_target: float, theta_micro: int) -> None:
        self._current_share_target = share_target
        self._current_theta = theta_micro
        log.info(
            "Difficulty update shareTarget=%.6f theta=%d",
            share_target,
            theta_micro,
        )

    async def _handle_notify(self, job: Dict[str, Any]) -> None:
        header = job.get("header") or {}
        sign_hex = job.get("signBytes") or header.get("signBytes")
        if not isinstance(sign_hex, str):
            log.warning("Job missing signBytes; skipping job %s", job.get("jobId"))
            return

        mix_hex = None
        hints = job.get("hints") or {}
        if isinstance(hints, dict):
            mix_hex = hints.get("mixSeed")
        if mix_hex is None:
            mix_hex = header.get("mixSeed")

        theta = int(job.get("thetaMicro") or header.get("thetaMicro") or self._current_theta)
        share_target = float(job.get("shareTarget") or self._current_share_target)
        height = int(job.get("height") or header.get("number") or 0)

        job_cfg = JobConfig(
            job_id=str(job.get("jobId") or header.get("hash") or "unknown"),
            header_bytes=_decode_hex(sign_hex),
            mix_seed=_decode_hex(mix_hex, fallback_bytes=32),
            theta_micro=max(theta, 1),
            share_target=max(share_target, 1e-9),
            height=height,
            hints=hints if isinstance(hints, dict) else {},
            target_hex=job.get("target"),
        )

        self._active_job = job_cfg
        if self._scanner:
            self._scanner.set_job(job_cfg)
        log.info(
            "Loaded job jobId=%s height=%s theta=%s shareTarget=%.6f",
            job_cfg.job_id,
            job_cfg.height,
            job_cfg.theta_micro,
            job_cfg.share_target,
        )

    # ------------------- Share submission -------------------

    async def _share_consumer(self) -> None:
        while True:
            candidate = await self._share_queue.get()
            await self._submit_share(candidate)

    async def _submit_share(self, candidate: ShareCandidate) -> None:
        if not self._client:
            log.warning("Share ready but no Stratum client is active; dropping share.")
            return
        nonce_hex = hex(candidate.nonce)
        body = {
            "dRatio": candidate.d_ratio,
            "hMicro": candidate.h_micro,
        }
        hashshare = {"nonce": nonce_hex, "body": body}
        try:
            result = await self._client.submit_share(
                candidate.job.job_id, hashshare
            )
            accepted = result.get("accepted", False)
            is_block = result.get("isBlock", False)
            reason = result.get("reason")
            log.info(
                "Share submitted nonce=%s accepted=%s block=%s reason=%s",
                nonce_hex,
                accepted,
                is_block,
                reason,
            )
        except Exception as exc:
            log.error("Failed to submit share nonce=%s: %s", nonce_hex, exc)

    async def _connect_with_retries(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            client = StratumClient(host=self._host, port=self._port, agent=self._agent)
            client.on_notify = self._handle_notify
            client.on_set_difficulty = self._handle_set_difficulty
            try:
                await client.connect()
                await client.subscribe()
                await client.authorize(worker=self._worker, address=self._address)
                self._client = client
                log.info(
                    "Connected to Stratum %s:%s as worker=%s address=%s",
                    client.host,
                    client.port,
                    self._worker,
                    self._address,
                )
                return
            except Exception as exc:
                log.warning(
                    "Failed to connect to Stratum (%s). Retrying in %.1f s.",
                    exc,
                    delay,
                )
                with contextlib.suppress(Exception):
                    await client.close()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
        raise RuntimeError("Miner stopped before establishing Stratum connection.")


# Convenience runner ---------------------------------------------------------

async def run_miner(args: Any) -> None:
    device_kwargs: Dict[str, Any] = {}
    if getattr(args, "platform", None) is not None:
        device_kwargs["platform_index"] = args.platform
    if getattr(args, "device", None) is not None:
        device_kwargs["device_index"] = args.device

    miner = StratumOpenCLMiner(
        host=args.host,
        port=args.port,
        worker=args.worker,
        address=args.address,
        iterations=args.iterations,
        max_found=args.max_found,
        device_kwargs=device_kwargs,
    )

    await miner.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signame):
            loop.add_signal_handler(getattr(signal, signame), stop.set)
    log.info("Miner started. Press Ctrl+C to stop.")
    await stop.wait()
    await miner.stop()
