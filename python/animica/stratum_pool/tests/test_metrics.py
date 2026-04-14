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


@pytest.mark.asyncio
async def test_pps_accounting_credits_accepted_shares():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        job_manager,
        DummyServer(),
    )
    session = Session(session_id="s1", writer=None, worker="worker-pps", address="anim1pps")
    job = StratumJob(
        job_id="job-pps",
        header={"number": 10},
        share_target=0.25,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
    )

    await metrics.record_share(
        session,
        job,
        submit_params={"d_ratio": 0.25},
        ok=True,
        reason=None,
        is_block=False,
        tx_count=0,
    )
    detail = metrics.miner_detail("worker-pps")
    assert detail["pool_mode"] == "pps"
    assert detail["credit_pps"] == "250"
    assert detail["credit_solo"] == "0"
    summary = metrics.accounting_summary()
    assert summary["total_credit"] == "250"
    assert summary["accepted_shares"] == 1


@pytest.mark.asyncio
async def test_solo_accounting_only_credits_blocks():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="solo"),
        job_manager,
        DummyServer(),
    )
    session = Session(session_id="s1", writer=None, worker="worker-solo", address="anim1solo")
    job = StratumJob(
        job_id="job-solo",
        header={"number": 11},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 5000}},
    )

    await metrics.record_share(
        session,
        job,
        submit_params={"d_ratio": 1.0},
        ok=True,
        reason=None,
        is_block=False,
        tx_count=0,
    )
    await metrics.record_share(
        session,
        job,
        submit_params={"d_ratio": 1.0},
        ok=True,
        reason=None,
        is_block=True,
        tx_count=1,
    )

    detail = metrics.miner_detail("worker-solo")
    assert detail["pool_mode"] == "solo"
    assert detail["credit_pps"] == "0"
    assert detail["credit_solo"] == "5000"
    summary = metrics.accounting_summary()
    assert summary["total_credit"] == "5000"
    assert summary["accepted_blocks"] == 1
