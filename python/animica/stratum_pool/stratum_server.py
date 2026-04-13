from __future__ import annotations

import asyncio
import logging
from typing import Optional

from mining.stratum_server import StratumJob, StratumServer

from .config import PoolConfig
from .core import MiningCoreAdapter, MiningJob
from .job_manager import JobManager


class PoolShareValidator:
    def __init__(
        self, adapter: MiningCoreAdapter, *, logger: Optional[logging.Logger] = None
    ) -> None:
        self._adapter = adapter
        self._log = logger or logging.getLogger("animica.stratum_pool.validator")

    async def validate(self, job: StratumJob, submit_params):
        mining_job = MiningJob(
            job_id=job.job_id,
            header=job.header,
            theta_micro=job.theta_micro,
            share_target=job.share_target,
            height=submit_params.get("height") or job.height or 0,
            hints=job.hints,
            target=job.target,
            sign_bytes=job.sign_bytes,
            template_id=(job.raw or {}).get("templateId")
            if isinstance(job.raw, dict)
            else None,
            parent_hash=job.parent_hash,
            expires_at=job.expires_at,
            raw=job.raw or {},
        )
        try:
            return await self._adapter.validate_and_submit_share(
                mining_job, submit_params
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning("share validation failed", exc_info=True)
            return False, str(exc), False, 0


class StratumPoolServer:
    def __init__(
        self,
        adapter: MiningCoreAdapter,
        config: PoolConfig,
        job_manager: JobManager,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._job_manager = job_manager
        self._log = logger or logging.getLogger("animica.stratum_pool.server")
        self._validator = PoolShareValidator(adapter, logger=logger)
        self._last_published_job_id: Optional[str] = None
        self._last_diff_tuple: Optional[tuple[float, int]] = None
        self._server = StratumServer(
            host=config.host,
            port=config.port,
            default_share_target=config.min_difficulty,
            default_theta_micro=0,
            max_cached_jobs=128,
            validator=self._validator,
        )

    def _resolve_share_target(self, requested: float) -> float:
        value = float(requested or 0.0)
        if value <= 0.0:
            value = float(self._config.min_difficulty)

        lower = max(1e-9, float(self._config.min_difficulty))
        upper = min(1.0, float(self._config.max_difficulty))
        if upper < lower:
            upper = lower

        clamped = min(max(value, lower), upper)
        if clamped != value:
            self._log.warning(
                "share_target_clamped",
                extra={
                    "requested": value,
                    "clamped": clamped,
                    "min_difficulty": self._config.min_difficulty,
                    "max_difficulty": self._config.max_difficulty,
                },
            )
        if clamped >= 0.95:
            self._log.warning(
                "share_target_near_block_target",
                extra={
                    "share_target": clamped,
                    "note": "Shares will be close to full block difficulty.",
                },
            )
        return clamped

    async def start(self) -> None:
        self._job_manager.subscribe(self._on_new_job)
        self._job_manager.start()
        await self._server.start()

    async def stop(self) -> None:
        await self._server.stop()
        await self._job_manager.stop()

    async def _on_new_job(self, job: MiningJob) -> None:
        header = dict(job.header or {})
        if job.sign_bytes:
            header.setdefault("signBytes", job.sign_bytes)
        if job.target:
            header.setdefault("target", job.target)
        if job.height:
            header.setdefault("number", job.height)
        if job.theta_micro:
            header.setdefault("thetaMicro", job.theta_micro)
            header.setdefault("thetaTargetMicro", job.theta_micro)
            header.setdefault("theta_target_micro", job.theta_micro)
        share_target = self._resolve_share_target(
            job.share_target or self._config.min_difficulty
        )
        stratum_job = StratumJob(
            job_id=job.job_id,
            header=header,
            share_target=share_target,
            theta_micro=job.theta_micro,
            hints=job.hints,
            target=job.target,
            sign_bytes=job.sign_bytes or header.get("signBytes"),
            height=job.height,
            parent_hash=job.parent_hash,
            expires_at=job.expires_at,
            raw=job.raw if isinstance(job.raw, dict) else None,
        )
        diff_tuple = (float(stratum_job.share_target), int(stratum_job.theta_micro))
        if self._last_diff_tuple != diff_tuple:
            await self._server.set_global_difficulty(
                stratum_job.share_target,
                stratum_job.theta_micro,
            )
            self._last_diff_tuple = diff_tuple

        clean_jobs = stratum_job.job_id != self._last_published_job_id
        await self._server.publish_job(stratum_job, clean_jobs=clean_jobs)
        self._last_published_job_id = stratum_job.job_id

    async def wait_closed(self) -> None:
        while True:
            await asyncio.sleep(1)

    @property
    def stratum(self) -> StratumServer:
        return self._server

    def stats(self) -> dict:
        return self._server.stats()

    def session_snapshots(self):
        return self._server.session_snapshots()
