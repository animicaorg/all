import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.job_manager import JobManager


@dataclass
class DummyJob:
    job_id: str
    header: dict
    theta_micro: int
    share_target: float
    height: int
    hints: dict


class DummyAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def get_new_job(self) -> DummyJob:
        self.calls += 1
        return DummyJob(
            job_id=str(self.calls),
            header={"height": self.calls},
            theta_micro=1,
            share_target=0.1,
            height=self.calls,
            hints={},
        )


@pytest.mark.asyncio
async def test_job_manager_publishes_updates():
    adapter = DummyAdapter()
    cfg = PoolConfig(poll_interval=0.01)
    manager = JobManager(adapter, cfg)

    seen: list[str] = []

    async def on_job(job):
        seen.append(job.job_id)

    manager.subscribe(on_job)
    manager.start()
    await asyncio.sleep(0.05)
    await manager.stop()

    assert seen, "expected callbacks to run"
    assert seen[0] == "1"
