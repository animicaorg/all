import asyncio
import sys
import time
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
    target: str | None = None
    template_id: str | None = None
    parent_hash: str | None = None
    issued_at: float | None = None
    expires_at: float | None = None
    head_hash_at_issue: str | None = None
    mempool_fingerprint: str | None = None


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
            template_id=str(self.calls),
            parent_hash="0x" + "11" * 32,
        )

    async def get_head_snapshot(self) -> dict:
        return {"hash": "0x" + "11" * 32, "height": 1}


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

    assert len(seen) >= 1
    assert seen[0] == "1"


@pytest.mark.asyncio
async def test_job_manager_request_refresh_wakes_poll_loop():
    adapter = DummyAdapter()
    cfg = PoolConfig(poll_interval=10.0)
    manager = JobManager(adapter, cfg)

    seen: list[str] = []
    second_job = asyncio.Event()

    async def on_job(job):
        seen.append(job.job_id)
        if job.job_id == "2":
            second_job.set()

    manager.subscribe(on_job)
    manager.start()

    await asyncio.sleep(0.05)
    manager.request_refresh()
    await asyncio.wait_for(second_job.wait(), timeout=0.5)
    await manager.stop()

    assert seen[:2] == ["1", "2"]


@pytest.mark.asyncio
async def test_job_manager_does_not_churn_templates_without_reason():
    adapter = DummyAdapter()
    cfg = PoolConfig(poll_interval=0.01)
    manager = JobManager(adapter, cfg)
    manager._periodic_refresh_s = 0.05
    manager._next_periodic_refresh_at = time.time()

    seen: list[str] = []

    async def on_job(job):
        seen.append(job.job_id)

    manager.subscribe(on_job)
    manager.start()
    await asyncio.sleep(0.25)
    await manager.stop()

    assert seen == ["1"]
    assert adapter.calls >= 2


@pytest.mark.asyncio
async def test_job_manager_refreshes_after_template_expiry():
    now = time.time()

    class ExpiringAdapter(DummyAdapter):
        async def get_new_job(self) -> DummyJob:
            self.calls += 1
            expires_at = now + 0.1 if self.calls == 1 else now + 10.0
            return DummyJob(
                job_id=str(self.calls),
                header={"height": self.calls},
                theta_micro=1,
                share_target=0.1,
                height=self.calls,
                hints={},
                template_id=str(self.calls),
                parent_hash="0x" + "22" * 32,
                expires_at=expires_at,
            )

        async def get_head_snapshot(self) -> dict:
            return {"hash": "0x" + "22" * 32, "height": 1}

    adapter = ExpiringAdapter()
    cfg = PoolConfig(poll_interval=0.01)
    manager = JobManager(adapter, cfg)

    seen: list[str] = []

    async def on_job(job):
        seen.append(job.job_id)

    manager.subscribe(on_job)
    manager.start()
    await asyncio.sleep(0.35)
    await manager.stop()

    assert seen[:2] == ["1", "2"]


def test_job_manager_backoff_resets_after_success():
    cfg = PoolConfig(poll_interval=0.1)
    manager = JobManager(DummyAdapter(), cfg)

    first = manager._next_wait(success=False)
    second = manager._next_wait(success=False)
    reset = manager._next_wait(success=True)

    assert first == pytest.approx(0.2)
    assert second == pytest.approx(0.4)
    assert reset == pytest.approx(cfg.poll_interval)
    assert manager._failure_streak == 0
