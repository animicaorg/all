from __future__ import annotations

import asyncio
import logging
from typing import Optional

from mining.stratum_server import StratumJob, StratumServer

from .config import PoolConfig
from .core import MiningCoreAdapter, MiningJob
from .job_manager import JobManager


class PoolShareValidator:
    def __init__(self, adapter: MiningCoreAdapter, *, logger: Optional[logging.Logger] = None) -> None:
        self._adapter = adapter
        self._log = logger or logging.getLogger("animica.stratum_pool.validator")

    async def validate(self, job: StratumJob, submit_params):
        mining_job = MiningJob(
            job_id=job.job_id,
            header=job.header,
            theta_micro=job.theta_micro,
            share_target=job.share_target,
            height=submit_params.get("height") or 0,
            hints=job.hints,
        )
        try:
            return await self._adapter.validate_and_submit_share(mining_job, submit_params)
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
        self._server = StratumServer(
            host=config.host,
            port=config.port,
            default_share_target=config.min_difficulty,
            default_theta_micro=0,
            validator=self._validator,
        )

    async def start(self) -> None:
        self._job_manager.subscribe(self._on_new_job)
        self._job_manager.start()
        await self._server.start()

    async def stop(self) -> None:
        await self._server.stop()
        await self._job_manager.stop()

    async def _on_new_job(self, job: MiningJob) -> None:
        stratum_job = StratumJob(
            job_id=job.job_id,
            header=job.header,
            share_target=job.share_target or self._config.min_difficulty,
            theta_micro=job.theta_micro,
            hints=job.hints,
        )
        await self._server.publish_job(stratum_job)

    async def wait_closed(self) -> None:
        while True:
            await asyncio.sleep(1)
