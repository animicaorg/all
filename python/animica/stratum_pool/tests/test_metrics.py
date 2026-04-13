import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.metrics import PoolMetrics
from mining.stratum_server import Session, StratumJob


class DummyJobManager:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self.current = None

    def current_job(self):
        return self.current

    def request_refresh(self) -> None:
        self.refresh_calls += 1


class DummyServer:
    def stats(self):
        return {}

    def session_snapshots(self):
        return []


@pytest.mark.asyncio
async def test_record_share_only_tracks_accepted_blocks():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(PoolConfig(db_url=""), job_manager, DummyServer())
    session = Session(session_id="s1", writer=None, worker="worker-1", address="anim1qqq")
    job = StratumJob(
        job_id="job-1",
        header={"number": 7},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 123456789}},
    )
    job_manager.current = SimpleNamespace(
        height=7,
        header={"hash": "0xabc"},
        raw={"coinbase": {"amount": 123456789}},
    )

    await metrics.record_share(
        session,
        job,
        submit_params={},
        ok=False,
        reason="rpc:-32602:RPC error -32602: unknown or stale jobId",
        is_block=True,
        tx_count=0,
    )
    assert len(metrics._block_events) == 0
    assert job_manager.refresh_calls == 0

    await metrics.record_share(
        session,
        job,
        submit_params={},
        ok=True,
        reason=None,
        is_block=True,
        tx_count=2,
    )
    assert len(metrics._block_events) == 1
    assert metrics._block_events[0]["job_id"] == "job-1"
    assert metrics._block_events[0]["worker"] == "worker-1"
    assert metrics._block_events[0]["address"] == "anim1qqq"
    assert job_manager.refresh_calls == 1
    assert metrics.pool_summary()["blocks_found_total"] == 1
    assert metrics.pool_summary()["round_estimated_reward"] == "123456789"
    assert metrics.miner_detail("worker-1")["blocks_found"] == 1
    assert metrics.recent_blocks()["items"][0]["worker"] == "worker-1"
    assert metrics.recent_blocks()["items"][0]["reward"] == "123456789"


@pytest.mark.asyncio
async def test_record_share_stale_template_requests_refresh():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(PoolConfig(db_url=""), job_manager, DummyServer())
    session = Session(session_id="s1", writer=None, worker="worker-1", address="anim1qqq")
    job = StratumJob(
        job_id="job-stale",
        header={"number": 7},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 123456789}},
    )
    job_manager.current = SimpleNamespace(
        height=7,
        header={"hash": "0xabc"},
        raw={"coinbase": {"amount": 123456789}},
    )

    await metrics.record_share(
        session,
        job,
        submit_params={},
        ok=False,
        reason="rpc:-32063:RPC error -32063: stale template",
        is_block=True,
        tx_count=0,
    )
    assert job_manager.refresh_calls == 1
