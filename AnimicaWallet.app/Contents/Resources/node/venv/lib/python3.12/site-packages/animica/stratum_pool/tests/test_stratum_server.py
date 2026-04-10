import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.core import MiningJob
from animica.stratum_pool.job_manager import JobManager
from animica.stratum_pool.stratum_server import PoolShareValidator, StratumPoolServer


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
        raw={"templateId": "job-1", "header": {"signBytes": "0x1234"}},
    )

    await server._on_new_job(job)

    server.stratum.set_global_difficulty.assert_awaited_once_with(0.5, 1_000_000)
    published_job = server.stratum.publish_job.await_args.args[0]
    assert published_job.header["thetaMicro"] == 1_000_000
    assert published_job.header["thetaTargetMicro"] == 1_000_000
    assert published_job.header["theta_target_micro"] == 1_000_000
    assert published_job.raw == job.raw


@pytest.mark.asyncio
async def test_pool_share_validator_preserves_template_raw():
    class CapturingAdapter:
        def __init__(self) -> None:
            self.seen_job = None

        async def validate_and_submit_share(self, job, submit_params):
            self.seen_job = job
            return True, None, False, 0

    adapter = CapturingAdapter()
    validator = PoolShareValidator(adapter)

    from mining.stratum_server import StratumJob

    raw_template = {
        "templateId": "job-1",
        "header": {"parentHash": "0x" + "11" * 32, "height": 7},
        "target": "0x" + "ff" * 32,
        "parent": {"height": 6, "hash": "0x" + "11" * 32},
        "txs": [],
    }
    stratum_job = StratumJob(
        job_id="job-1",
        header={"number": 7, "signBytes": "0x1234"},
        share_target=1.0,
        theta_micro=1_000_000,
        target="0x" + "ff" * 32,
        sign_bytes="0x1234",
        height=7,
        raw=raw_template,
    )

    accepted, reason, is_block, tx_count = await validator.validate(
        stratum_job,
        {"hashshare": {"nonce": "0x01", "body": {}}},
    )

    assert accepted is True
    assert reason is None
    assert is_block is False
    assert tx_count == 0
    assert adapter.seen_job is not None
    assert adapter.seen_job.height == 7
    assert adapter.seen_job.raw == raw_template
