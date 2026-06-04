"""Phase-1 gate: rig-rental credit redirect in PoolMetrics.

Verifies that while a rig is under an active rental, its accepted shares are
credited to the renter's address; and that once the window ends the credit
reverts to the owner (the rig goes back to mining for itself).
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.metrics import PoolMetrics, RentalConflict
from mining.stratum_server import Session, StratumJob


class DummyJobManager:
    def request_refresh(self) -> None:
        pass


class DummyServer:
    def stats(self):
        return {}

    def session_snapshots(self):
        return []


OWNER = "anim1ownerxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
RENTER = "anim1renterxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
WORKER = "rig-01"


def _metrics(tmp_path) -> PoolMetrics:
    db = tmp_path / "pool.db"
    return PoolMetrics(
        PoolConfig(db_url=f"sqlite:///{db}", pool_mode="pps"),
        DummyJobManager(),
        DummyServer(),
    )


def _job() -> StratumJob:
    return StratumJob(
        job_id="job-1",
        header={"number": 7},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": 1_000_000_000}},
    )


async def _accept_share(metrics: PoolMetrics, worker: str, address: str) -> None:
    session = Session(session_id="s1", writer=None, worker=worker, address=address)
    await metrics.record_share(
        session,
        _job(),
        submit_params={"d_ratio": 1.0},
        ok=True,
        reason=None,
        is_block=False,
        tx_count=0,
    )
    metrics._run_housekeeping(force=True)  # flush deferred writes


def _accepted_shares(metrics: PoolMetrics, address: str) -> int:
    with metrics._db_lock:
        row = metrics._db.execute(
            "SELECT COALESCE(SUM(accepted_shares),0) FROM worker_balances WHERE address=?",
            (address,),
        ).fetchone()
    return int(row[0] or 0)


@pytest.mark.asyncio
async def test_credit_redirects_to_renter_then_reverts(tmp_path):
    metrics = _metrics(tmp_path)

    # Baseline: owner mines for themselves.
    await _accept_share(metrics, WORKER, OWNER)
    assert _accepted_shares(metrics, OWNER) == 1
    assert _accepted_shares(metrics, RENTER) == 0

    # Engage a rental covering now → owner's rig credits the renter.
    now = time.time()
    metrics.upsert_rental_assignment(
        id="rent-1",
        owner_worker=WORKER,
        owner_address=OWNER,
        coins="ANM",
        anm_mode="pps",
        renter_anm_address=RENTER,
        start_ts=now - 5,
        end_ts=now + 3600,
    )
    await _accept_share(metrics, WORKER, OWNER)
    assert _accepted_shares(metrics, RENTER) == 1, "renter should be credited during window"
    assert _accepted_shares(metrics, OWNER) == 1, "owner credit must not increase during rental"

    # A second active rental for the same rig is rejected.
    with pytest.raises(RentalConflict):
        metrics.upsert_rental_assignment(
            id="rent-2",
            owner_worker=WORKER,
            owner_address=OWNER,
            coins="ANM",
            anm_mode="pps",
            renter_anm_address=RENTER,
            start_ts=now,
            end_ts=now + 60,
        )

    # Cancel → credit reverts to the owner immediately.
    assert metrics.cancel_rental_assignment("rent-1") is True
    await _accept_share(metrics, WORKER, OWNER)
    assert _accepted_shares(metrics, OWNER) == 2, "owner should mine for self after cancel"
    assert _accepted_shares(metrics, RENTER) == 1


@pytest.mark.asyncio
async def test_expired_window_reverts_without_cancel(tmp_path):
    metrics = _metrics(tmp_path)
    now = time.time()
    # Rental that already ended.
    metrics.upsert_rental_assignment(
        id="rent-old",
        owner_worker=WORKER,
        owner_address=OWNER,
        coins="ANM",
        anm_mode="pps",
        renter_anm_address=RENTER,
        start_ts=now - 7200,
        end_ts=now - 10,
    )
    assert metrics._active_rental_for(WORKER, OWNER, now) is None
    await _accept_share(metrics, WORKER, OWNER)
    assert _accepted_shares(metrics, OWNER) == 1
    assert _accepted_shares(metrics, RENTER) == 0


@pytest.mark.asyncio
async def test_window_stats_measures_uptime(tmp_path):
    metrics = _metrics(tmp_path)
    await _accept_share(metrics, WORKER, OWNER)
    now = time.time()
    stats = metrics.rig_window_stats(
        worker=WORKER, address=OWNER, start_ts=now - 600, end_ts=now + 1
    )
    assert stats["active_buckets"] >= 1
    assert stats["measured_uptime_seconds"] >= 1
