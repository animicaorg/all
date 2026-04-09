import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.core import MiningJob
from animica.stratum_pool.job_manager import JobManager
from animica.stratum_pool.stratum_server import StratumPoolServer


class DummyAdapter:
    pass


@pytest.mark.asyncio
async def test_on_new_job_updates_theta_and_broadcasts_difficulty():
    server = StratumPoolServer(DummyAdapter(), PoolConfig(), JobManager(DummyAdapter(), PoolConfig()))
    server.stratum.set_global_difficulty = AsyncMock()
    server.stratum.publish_job = AsyncMock()

    job = MiningJob(
        job_id="job-1",
        header={"signBytes": "0x1234"},
        theta_micro=1_000_000,
        share_target=0.5,
        height=7,
        target="0x99",
        sign_bytes="0x1234",
        hints={"mixSeed": "0x55"},
    )

    await server._on_new_job(job)

    server.stratum.set_global_difficulty.assert_awaited_once_with(0.5, 1_000_000)
    published_job = server.stratum.publish_job.await_args.args[0]
    assert published_job.header["thetaMicro"] == 1_000_000
    assert published_job.header["thetaTargetMicro"] == 1_000_000
    assert published_job.header["theta_target_micro"] == 1_000_000

