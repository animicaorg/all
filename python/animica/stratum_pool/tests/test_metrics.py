import sys
import time
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
async def test_pps_block_share_without_ratio_credits_full_reward():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        job_manager,
        DummyServer(),
    )
    session = Session(
        session_id="s1",
        writer=None,
        worker="worker-pps-block",
        address="anim1ppsblock",
    )
    job = StratumJob(
        job_id="job-pps-block",
        header={"number": 10},
        share_target=0.01,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
    )

    await metrics.record_share(
        session,
        job,
        submit_params={},
        ok=True,
        reason=None,
        is_block=True,
        tx_count=1,
    )

    detail = metrics.miner_detail("worker-pps-block")
    assert detail["pool_mode"] == "pps"
    assert detail["credit_pps"] == "1000"
    summary = metrics.accounting_summary()
    assert summary["total_credit"] == "1000"
    assert summary["accepted_blocks"] == 1


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


@pytest.mark.asyncio
async def test_payout_debits_available_credit_and_tracks_due_amount():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        job_manager,
        DummyServer(),
    )
    session = Session(
        session_id="s1",
        writer=None,
        worker="worker-pay",
        address="anim1pay",
    )
    job = StratumJob(
        job_id="job-pay",
        header={"number": 12},
        share_target=0.5,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
    )

    await metrics.record_share(
        session,
        job,
        submit_params={"d_ratio": 0.5},
        ok=True,
        reason=None,
        is_block=False,
        tx_count=0,
    )
    due_before = metrics.payout_due_addresses(min_amount=1, limit=10)
    assert due_before
    assert due_before[0]["address"] == "anim1pay"
    assert due_before[0]["amount"] == 500

    applied = metrics.record_payout_sent(
        address="anim1pay",
        amount=300,
        tx_hash="0x" + "ab" * 32,
    )
    assert applied == 300

    summary = metrics.accounting_summary()
    assert summary["gross_credit"] == "500"
    assert summary["paid_out_total"] == "300"
    assert summary["total_credit"] == "200"

    due_after = metrics.payout_due_addresses(min_amount=1, limit=10)
    assert due_after
    assert due_after[0]["amount"] == 200


def test_payout_status_includes_interval_and_countdown():
    metrics = PoolMetrics(
        PoolConfig(db_url="", payout_interval_seconds=60, payout_min_amount=10),
        DummyJobManager(),
        DummyServer(),
    )
    metrics.set_next_payout_at(time.time() + 25)
    status = metrics.payout_status()
    assert status["payouts_enabled"] is True
    assert status["payout_interval_seconds"] == 60.0
    assert status["payout_min_amount"] == 10
    countdown = int(status["payout_countdown_seconds"] or 0)
    assert 0 <= countdown <= 25


def test_mined_reward_in_window_counts_only_recent_pool_blocks():
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    now = 1_700_000_000.0
    metrics._block_events.appendleft(  # noqa: SLF001
        {"timestamp": now - 10, "reward": 300, "found_by_pool": True}
    )
    metrics._block_events.appendleft(  # noqa: SLF001
        {"timestamp": now - 50, "reward": 200, "found_by_pool": True}
    )
    metrics._block_events.appendleft(  # noqa: SLF001
        {"timestamp": now - 5, "reward": 900, "found_by_pool": False}
    )
    metrics._block_events.appendleft(  # noqa: SLF001
        {"timestamp": now - 120, "reward": 500, "found_by_pool": True}
    )

    assert metrics.mined_reward_in_window(window_seconds=60, now=now) == 500


@pytest.mark.asyncio
async def test_payout_due_addresses_respects_max_total_amount():
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    job_a = StratumJob(
        job_id="job-cap-a",
        header={"number": 20},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
    )
    job_b = StratumJob(
        job_id="job-cap-b",
        header={"number": 21},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
    )
    session_a = Session(session_id="sa", writer=None, worker="worker-a", address="anim1aaa")
    session_b = Session(session_id="sb", writer=None, worker="worker-b", address="anim1bbb")

    await metrics.record_share(
        session_a,
        job_a,
        submit_params={"d_ratio": 1.0},
        ok=True,
        reason=None,
        is_block=False,
        tx_count=0,
    )
    await metrics.record_share(
        session_b,
        job_b,
        submit_params={"d_ratio": 1.0},
        ok=True,
        reason=None,
        is_block=False,
        tx_count=0,
    )

    uncapped = metrics.payout_due_addresses(min_amount=1, limit=10)
    assert [item["amount"] for item in uncapped] == [1000, 1000]

    capped = metrics.payout_due_addresses(
        min_amount=1,
        limit=10,
        max_total_amount=1500,
    )
    assert [item["amount"] for item in capped] == [1000, 500]
    assert sum(int(item["amount"]) for item in capped) == 1500

    below_min = metrics.payout_due_addresses(
        min_amount=200,
        limit=10,
        max_total_amount=150,
    )
    assert below_min == []
