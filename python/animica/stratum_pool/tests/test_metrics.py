import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.asic import Sha256Job, Sha256Session
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


class StaticSessionServer(DummyServer):
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)

    def session_snapshots(self):
        return list(self._snapshots)


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
async def test_address_attribution_aggregates_across_worker_names():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(PoolConfig(db_url=""), job_manager, DummyServer())
    job = StratumJob(
        job_id="job-addr-agg",
        header={"number": 9},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
    )
    session_a = Session(session_id="sa", writer=None, worker="rig-a", address="anim1agg")
    session_b = Session(session_id="sb", writer=None, worker="rig-b", address="anim1agg")

    await metrics.record_share(
        session_a,
        job,
        submit_params={},
        ok=True,
        reason=None,
        is_block=False,
        tx_count=0,
    )
    await metrics.record_share(
        session_b,
        job,
        submit_params={},
        ok=True,
        reason=None,
        is_block=True,
        tx_count=1,
    )

    by_worker = metrics.miner_detail("rig-a")
    by_address = metrics.miner_detail("anim1agg")
    assert by_worker["address"] == "anim1agg"
    assert by_worker["shares_accepted"] == 2
    assert by_worker["blocks_found"] == 1
    assert by_address["shares_accepted"] == 2
    assert by_address["blocks_found"] == 1

    miners = metrics.miners()
    assert miners["total"] == 1
    assert miners["items"][0]["worker_id"] == "anim1agg"
    assert miners["items"][0]["shares_accepted"] == 2
    assert miners["items"][0]["blocks_found"] == 1


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
    # PPS credits a share its EXPECTED value = reward × P(share also solves a block)
    # = reward × block_target/share_target = reward × exp(-θ·(1-ratio)/MICRO).
    # 1000 × exp(-1e6·(1-0.25)/1e6) = 1000 × exp(-0.75) = 472. (The legacy
    # reward×ratio = 250 over-priced low-difficulty shares — see the vardiff rework.)
    assert detail["credit_pps"] == "472"
    assert detail["credit_solo"] == "0"
    summary = metrics.accounting_summary()
    assert summary["total_credit"] == "472"
    assert summary["accepted_shares"] == 1


@pytest.mark.asyncio
async def test_pps_share_credit_ledger_disabled_by_default(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'pool_pps_ledger_default.db'}"
    metrics = PoolMetrics(
        PoolConfig(db_url=db_url, pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    session = Session(
        session_id="s-ledger-default",
        writer=None,
        worker="worker-ledger-default",
        address="anim1ledgerdefault",
    )
    job = StratumJob(
        job_id="job-ledger-default",
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

    with metrics._db_lock:  # noqa: SLF001
        ledger_rows = metrics._db.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM accounting_ledger WHERE event = 'pps_share_credit'"
        ).fetchone()
    assert int((ledger_rows or [0])[0] or 0) == 0
    # PPS expected value: 1000 × exp(-θ·(1-0.5)/MICRO) = 1000 × exp(-0.5) = 606.
    assert metrics.accounting_summary()["total_credit"] == "606"


@pytest.mark.asyncio
async def test_pps_share_credit_ledger_can_be_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMICA_POOL_LEDGER_RECORD_PPS_SHARES", "1")
    db_url = f"sqlite:///{tmp_path / 'pool_pps_ledger_enabled.db'}"
    metrics = PoolMetrics(
        PoolConfig(db_url=db_url, pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    session = Session(
        session_id="s-ledger-enabled",
        writer=None,
        worker="worker-ledger-enabled",
        address="anim1ledgerenabled",
    )
    job = StratumJob(
        job_id="job-ledger-enabled",
        header={"number": 13},
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

    with metrics._db_lock:  # noqa: SLF001
        ledger_rows = metrics._db.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM accounting_ledger WHERE event = 'pps_share_credit'"
        ).fetchone()
    assert int((ledger_rows or [0])[0] or 0) == 1
    # PPS expected value: 1000 × exp(-θ·(1-0.25)/MICRO) = 1000 × exp(-0.75) = 472.
    assert metrics.accounting_summary()["total_credit"] == "472"


@pytest.mark.asyncio
async def test_record_share_parses_address_from_v1_style_worker_identity():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        job_manager,
        DummyServer(),
    )
    session = Sha256Session(
        writer=None,
        extranonce1="abcd1234",
        extranonce2_size=4,
        difficulty=1.0,
        worker="anim1asicpay.worker-02",
    )
    job = Sha256Job(
        job_id="job-asic",
        prevhash="00" * 32,
        coinb1="",
        coinb2="",
        merkle_branch=[],
        version="20000000",
        nbits="1d00ffff",
        ntime="00000000",
        clean_jobs=True,
        target=1,
        difficulty=1.0,
        height=30,
        raw={"coinbaseValue": 2000},
    )

    await metrics.record_share(
        session,
        job,
        submit_params={"shareTarget": 0.5},
        ok=True,
        reason=None,
        is_block=False,
        tx_count=0,
    )

    due = metrics.payout_due_addresses(min_amount=1, limit=10)
    assert due
    assert due[0]["address"] == "anim1asicpay"
    assert due[0]["amount"] == 1000
    detail = metrics.miner_detail("worker-02")
    assert detail["address"] == "anim1asicpay"
    assert detail["shares_accepted"] == 1


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
async def test_both_mode_reports_per_miner_pool_mode():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="both"),
        job_manager,
        DummyServer(),
    )
    job = StratumJob(
        job_id="job-both",
        header={"number": 21},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
    )
    pps_session = Session(
        session_id="pps-session",
        writer=None,
        worker="rig-pps",
        address="anim1ppsminer",
        pool_mode="pps",
    )
    solo_session = Session(
        session_id="solo-session",
        writer=None,
        worker="rig-solo",
        address="anim1solominer",
        pool_mode="solo",
    )

    await metrics.record_share(
        pps_session,
        job,
        submit_params={"d_ratio": 0.5, "_pool_mode": "pps"},
        ok=True,
        reason=None,
        is_block=False,
        tx_count=0,
    )
    await metrics.record_share(
        solo_session,
        job,
        submit_params={"d_ratio": 1.0, "_pool_mode": "solo"},
        ok=True,
        reason=None,
        is_block=True,
        tx_count=1,
    )

    miners = metrics.miners()
    by_address = {item["address"]: item for item in miners["items"]}
    assert by_address["anim1ppsminer"]["pool_mode"] == "pps"
    assert by_address["anim1solominer"]["pool_mode"] == "solo"

    pps_detail = metrics.miner_detail("anim1ppsminer")
    solo_detail = metrics.miner_detail("anim1solominer")
    assert pps_detail["pool_mode"] == "pps"
    assert solo_detail["pool_mode"] == "solo"


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
    # PPS: 1000 × exp(-θ·(1-0.5)/MICRO) = 1000 × exp(-0.5) = 606.
    assert due_before[0]["amount"] == 606

    applied = metrics.record_payout_sent(
        address="anim1pay",
        amount=300,
        tx_hash="0x" + "ab" * 32,
    )
    assert applied == 300

    summary = metrics.accounting_summary()
    assert summary["gross_credit"] == "606"
    assert summary["paid_out_total"] == "300"
    assert summary["total_credit"] == "306"  # 606 credited − 300 paid

    due_after = metrics.payout_due_addresses(min_amount=1, limit=10)
    assert due_after
    assert due_after[0]["amount"] == 306


@pytest.mark.asyncio
async def test_mark_payout_dropped_releases_reserved_credit():
    job_manager = DummyJobManager()
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        job_manager,
        DummyServer(),
    )
    session = Session(
        session_id="s1",
        writer=None,
        worker="worker-drop",
        address="anim1drop",
    )
    job = StratumJob(
        job_id="job-drop",
        header={"number": 18},
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

    applied = metrics.record_payout_sent(
        address="anim1drop",
        amount=300,
        tx_hash="0x" + "ab" * 32,
        raw_tx="raw-payout",
        nonce=9,
    )
    assert applied == 300
    due_before = metrics.payout_due_addresses(min_amount=1, limit=10)
    # PPS credit 1000 × exp(-0.5) = 606, minus the 300 reserved by the payout.
    assert due_before and due_before[0]["amount"] == 306

    dropped = metrics.mark_payout_dropped(
        tx_hash="0x" + "ab" * 32,
        error="evicted from mempool",
        release_credit=True,
    )
    assert dropped is True

    summary = metrics.accounting_summary()
    assert summary["paid_out_total"] == "0"
    assert summary["total_credit"] == "606"  # dropped payout released back
    due_after = metrics.payout_due_addresses(min_amount=1, limit=10)
    assert due_after and due_after[0]["amount"] == 606
    assert metrics.pending_payout_submissions(limit=10) == []


def test_pending_payout_submissions_round_trip_with_sqlite(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'pool_metrics.db'}"

    metrics = PoolMetrics(
        PoolConfig(db_url=db_url, pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    metrics._apply_balance_delta(  # noqa: SLF001
        ts=time.time(),
        worker="worker-sqlite",
        address="anim1sqlite",
        pps_credit=800,
    )
    applied = metrics.record_payout_sent(
        address="anim1sqlite",
        amount=250,
        tx_hash="0x" + "cd" * 32,
        raw_tx="raw-sqlite",
        nonce=11,
    )
    assert applied == 250

    reloaded = PoolMetrics(
        PoolConfig(db_url=db_url, pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    pending = reloaded.pending_payout_submissions(limit=10)
    assert len(pending) == 1
    assert pending[0]["tx_hash"] == "0x" + "cd" * 32
    assert pending[0]["raw_tx"] == "raw-sqlite"
    assert pending[0]["nonce"] == 11
    assert pending[0]["amount"] == 250


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


def test_payout_available_budget_is_mined_minus_paid():
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    metrics._block_events.appendleft(  # noqa: SLF001
        {"timestamp": 100.0, "reward": 900, "found_by_pool": True}
    )
    metrics._block_events.appendleft(  # noqa: SLF001
        {"timestamp": 110.0, "reward": 200, "found_by_pool": False}
    )
    metrics._worker_balances_cache[("pps", "worker-a", "anim1aaa")] = {  # noqa: SLF001
        "paid_out": 250
    }
    metrics._worker_balances_cache[("pps", "worker-b", "anim1bbb")] = {  # noqa: SLF001
        "paid_out": 150
    }

    assert metrics.payout_available_budget() == 500


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
    assert [item["amount"] for item in capped] == [750, 750]
    assert sum(int(item["amount"]) for item in capped) == 1500

    below_min = metrics.payout_due_addresses(
        min_amount=200,
        limit=10,
        max_total_amount=150,
    )
    assert below_min == []


@pytest.mark.asyncio
async def test_payout_due_addresses_decays_disconnected_workers(monkeypatch):
    monkeypatch.setenv("ANIMICA_POOL_INACTIVE_SHARE_GRACE_SECONDS", "0")
    monkeypatch.setenv("ANIMICA_POOL_INACTIVE_SHARE_HALFLIFE_SECONDS", "1200")
    clock = [1_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    job = StratumJob(
        job_id="job-decay",
        header={"number": 30},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
    )
    session = Session(
        session_id="sd",
        writer=None,
        worker="worker-decay",
        address="anim1decay",
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

    clock[0] += 3600.0
    due = metrics.payout_due_addresses(min_amount=1, limit=10)
    assert due
    assert due[0]["address"] == "anim1decay"
    assert due[0]["amount"] == 125


@pytest.mark.asyncio
async def test_payout_due_addresses_keeps_connected_workers_at_full_credit(monkeypatch):
    monkeypatch.setenv("ANIMICA_POOL_INACTIVE_SHARE_GRACE_SECONDS", "0")
    monkeypatch.setenv("ANIMICA_POOL_INACTIVE_SHARE_HALFLIFE_SECONDS", "1200")
    clock = [1_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        DummyJobManager(),
        StaticSessionServer(
            [{"worker": "worker-live", "session_id": "live", "address": "anim1live"}]
        ),
    )
    job = StratumJob(
        job_id="job-live",
        header={"number": 31},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
    )
    session = Session(
        session_id="sl",
        writer=None,
        worker="worker-live",
        address="anim1live",
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

    clock[0] += 3600.0
    due = metrics.payout_due_addresses(min_amount=1, limit=10)
    assert due
    assert due[0]["address"] == "anim1live"
    assert due[0]["amount"] == 1000


@pytest.mark.asyncio
async def test_record_share_batches_sqlite_commits(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMICA_POOL_DB_BATCH_WRITE_SIZE", "1000")
    monkeypatch.setenv("ANIMICA_POOL_DB_BATCH_FLUSH_SECONDS", "3600")
    monkeypatch.setenv("ANIMICA_POOL_HOUSEKEEP_INTERVAL_SECONDS", "3600")
    db_url = f"sqlite:///{tmp_path / 'pool_batch.db'}"
    metrics = PoolMetrics(
        PoolConfig(db_url=db_url, pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    session = Session(
        session_id="batch-session",
        writer=None,
        worker="worker-batch",
        address="anim1batch",
    )
    job = StratumJob(
        job_id="job-batch",
        header={"number": 42},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1000}},
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

    assert metrics._db_pending_statements > 0  # noqa: SLF001
    with metrics._db_lock:  # noqa: SLF001
        row = metrics._db.execute("SELECT COUNT(*) FROM shares").fetchone()  # noqa: SLF001
    assert int((row or [0])[0] or 0) == 1

    metrics.close()
    assert metrics._db is None  # noqa: SLF001


def test_housekeeping_prunes_old_sqlite_history(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMICA_POOL_SHARE_RETENTION_SECONDS", "60")
    monkeypatch.setenv("ANIMICA_POOL_LEDGER_RETENTION_SECONDS", "60")
    monkeypatch.setenv("ANIMICA_POOL_PAYOUT_HISTORY_RETENTION_SECONDS", "60")
    db_url = f"sqlite:///{tmp_path / 'pool_retention.db'}"
    metrics = PoolMetrics(
        PoolConfig(db_url=db_url, pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    now = 2_000_000_000.0
    with metrics._db_lock:  # noqa: SLF001
        metrics._db.execute(  # noqa: SLF001
            """
            INSERT INTO shares (ts, worker, address, difficulty, status, job_id, height, is_block, tx_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now - 120, "worker-old", "anim1old", 1.0, "accepted", "job-old", 1, 0, 0),
        )
        metrics._db.execute(  # noqa: SLF001
            """
            INSERT INTO shares (ts, worker, address, difficulty, status, job_id, height, is_block, tx_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now - 30, "worker-new", "anim1new", 1.0, "accepted", "job-new", 2, 0, 0),
        )
        metrics._db.execute(  # noqa: SLF001
            """
            INSERT INTO accounting_ledger (ts, mode, worker, address, event, amount, job_id, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now - 120, "pps", "worker-old", "anim1old", "pps_share_credit", 10, "job-old", "{}"),
        )
        metrics._db.execute(  # noqa: SLF001
            """
            INSERT INTO accounting_ledger (ts, mode, worker, address, event, amount, job_id, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now - 30, "pps", "worker-new", "anim1new", "pps_share_credit", 10, "job-new", "{}"),
        )
        metrics._db.execute(  # noqa: SLF001
            """
            INSERT INTO payouts (
                ts, mode, address, amount, tx_hash, raw_tx, nonce, status, error,
                retry_count, last_retry_ts, next_retry_ts, confirmed_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now - 120,
                "pps",
                "anim1old",
                10,
                "0x" + "aa" * 32,
                None,
                None,
                "confirmed",
                None,
                0,
                None,
                None,
                now - 110,
            ),
        )
        metrics._db.execute(  # noqa: SLF001
            """
            INSERT INTO payouts (
                ts, mode, address, amount, tx_hash, raw_tx, nonce, status, error,
                retry_count, last_retry_ts, next_retry_ts, confirmed_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now - 120,
                "pps",
                "anim1keep",
                10,
                "0x" + "bb" * 32,
                None,
                None,
                "submitted",
                None,
                0,
                None,
                None,
                None,
            ),
        )
        metrics._commit_db_now(now_ts=now)  # noqa: SLF001

    metrics._run_housekeeping(now_ts=now + 20, force=True)  # noqa: SLF001

    with metrics._db_lock:  # noqa: SLF001
        share_count = metrics._db.execute("SELECT COUNT(*) FROM shares").fetchone()  # noqa: SLF001
        ledger_count = metrics._db.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM accounting_ledger"
        ).fetchone()
        payout_rows = metrics._db.execute(  # noqa: SLF001
            "SELECT status, tx_hash FROM payouts ORDER BY tx_hash ASC"
        ).fetchall()

    assert int((share_count or [0])[0] or 0) == 1
    assert int((ledger_count or [0])[0] or 0) == 1
    assert payout_rows == [("submitted", "0x" + "bb" * 32)]


def test_worker_balance_cache_prunes_stale_zero_credit(monkeypatch):
    monkeypatch.setenv("ANIMICA_POOL_WORKER_CACHE_TTL_SECONDS", "60")
    metrics = PoolMetrics(
        PoolConfig(db_url="", pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )
    metrics._worker_balances_cache[("pps", "worker-stale", "anim1stale")] = {  # noqa: SLF001
        "total_credit": 0,
        "paid_out": 0,
        "updated_ts": 100.0,
    }
    metrics._worker_balances_cache[("pps", "worker-credit", "anim1credit")] = {  # noqa: SLF001
        "total_credit": 25,
        "paid_out": 0,
        "updated_ts": 100.0,
    }
    metrics._run_housekeeping(now_ts=200.0, force=True)  # noqa: SLF001
    assert ("pps", "worker-stale", "anim1stale") not in metrics._worker_balances_cache  # noqa: SLF001
    assert ("pps", "worker-credit", "anim1credit") in metrics._worker_balances_cache  # noqa: SLF001
