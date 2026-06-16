"""ENA training-treasury fee: a configured fraction of each accepted share's
ANM credit is skimmed to the ENA treasury address, the miner keeps the rest,
and the treasury accrues as an ordinary credited balance (so the normal payout
scheduler pays it out)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.metrics import PoolMetrics
from mining.stratum_server import Session, StratumJob

TREASURY = "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
MINER = "anim1minerxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
WORKER = "rig-01"
REWARD = 1_000_000_000  # 1 ANM in nanm


class DummyJobManager:
    def request_refresh(self) -> None:
        pass


class DummyServer:
    def stats(self):
        return {}

    def session_snapshots(self):
        return []


def _metrics(tmp_path, *, fee_bps: int, mode: str = "pps") -> PoolMetrics:
    db = tmp_path / "pool.db"
    return PoolMetrics(
        PoolConfig(
            db_url=f"sqlite:///{db}",
            pool_mode=mode,
            ena_fee_bps=fee_bps,
            ena_treasury_address=TREASURY if fee_bps else "",
        ),
        DummyJobManager(),
        DummyServer(),
    )


def _job() -> StratumJob:
    return StratumJob(
        job_id="job-1",
        header={"number": 7},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": REWARD}},
    )


async def _accept_share(metrics: PoolMetrics, address: str, *, is_block: bool = False) -> None:
    session = Session(session_id="s1", writer=None, worker=WORKER, address=address)
    await metrics.record_share(
        session,
        _job(),
        submit_params={"d_ratio": 1.0},
        ok=True,
        reason=None,
        is_block=is_block,
        tx_count=0,
    )
    metrics._run_housekeeping(force=True)  # flush deferred writes


def _credit(metrics: PoolMetrics, address: str) -> int:
    with metrics._db_lock:
        row = metrics._db.execute(
            "SELECT COALESCE(SUM(total_credit),0) FROM worker_balances WHERE address=?",
            (address,),
        ).fetchone()
    return int(row[0] or 0)


@pytest.mark.asyncio
async def test_pps_share_skims_30pct_to_treasury(tmp_path):
    metrics = _metrics(tmp_path, fee_bps=3000)
    await _accept_share(metrics, MINER)
    # Miner keeps 70%, the ENA treasury accrues 30%, total is conserved.
    assert _credit(metrics, MINER) == 700_000_000
    assert _credit(metrics, TREASURY) == 300_000_000
    assert _credit(metrics, MINER) + _credit(metrics, TREASURY) == REWARD


@pytest.mark.asyncio
async def test_fee_disabled_credits_full_reward(tmp_path):
    metrics = _metrics(tmp_path, fee_bps=0)
    await _accept_share(metrics, MINER)
    assert _credit(metrics, MINER) == REWARD
    assert _credit(metrics, TREASURY) == 0


@pytest.mark.asyncio
async def test_treasury_share_is_not_self_skimmed(tmp_path):
    # A share credited to the treasury address must not be skimmed again.
    metrics = _metrics(tmp_path, fee_bps=3000)
    await _accept_share(metrics, TREASURY)
    assert _credit(metrics, TREASURY) == REWARD


@pytest.mark.asyncio
async def test_solo_block_skims_to_treasury(tmp_path):
    metrics = _metrics(tmp_path, fee_bps=3000, mode="solo")
    await _accept_share(metrics, MINER, is_block=True)
    assert _credit(metrics, MINER) == 700_000_000
    assert _credit(metrics, TREASURY) == 300_000_000
