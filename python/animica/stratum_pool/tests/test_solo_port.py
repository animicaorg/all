"""True solo port: a connection that arrives on the dedicated solo stratum port
(session.solo_forced=True) is credited 95% of every block it finds; the pool
keeps the remaining 5% (the block is mined to the pool address, so the retained
portion is simply not credited out). The ENA treasury fee does NOT apply to solo
blocks — the finder keeps the full post-fee 95%. Everything else (accounts,
worker rows, payouts) is shared with the normal pool."""

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


def _metrics(tmp_path, *, fee_bps: int = 3000, mode: str = "pps", solo_fee_bps: int = 500) -> PoolMetrics:
    db = tmp_path / "pool.db"
    return PoolMetrics(
        PoolConfig(
            db_url=f"sqlite:///{db}",
            pool_mode=mode,
            ena_fee_bps=fee_bps,
            ena_treasury_address=TREASURY if fee_bps else "",
            solo_fee_bps=solo_fee_bps,
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


async def _accept(metrics, address, *, solo_forced, is_block):
    session = Session(
        session_id="s1",
        writer=None,
        worker=WORKER,
        address=address,
        pool_mode="solo" if solo_forced else "pps",
        solo_forced=solo_forced,
    )
    await metrics.record_share(
        session, _job(), submit_params={"d_ratio": 1.0},
        ok=True, reason=None, is_block=is_block, tx_count=0,
    )
    metrics._run_housekeeping(force=True)


def _credit(metrics, address) -> int:
    with metrics._db_lock:
        row = metrics._db.execute(
            "SELECT COALESCE(SUM(total_credit),0) FROM worker_balances WHERE address=?",
            (address,),
        ).fetchone()
    return int(row[0] or 0)


@pytest.mark.asyncio
async def test_solo_port_block_credits_95pct_and_skips_ena(tmp_path):
    # Live-like pool: pps mode + 30% ENA fee. A solo-port (forced) block finder
    # keeps a clean 95%; the pool keeps 5% (uncredited); ENA is not skimmed.
    metrics = _metrics(tmp_path, fee_bps=3000, mode="pps", solo_fee_bps=500)
    await _accept(metrics, MINER, solo_forced=True, is_block=True)
    assert _credit(metrics, MINER) == 950_000_000
    assert _credit(metrics, TREASURY) == 0


@pytest.mark.asyncio
async def test_solo_fee_bps_configurable(tmp_path):
    # 10% pool fee → finder keeps 90%.
    metrics = _metrics(tmp_path, fee_bps=0, mode="pps", solo_fee_bps=1000)
    await _accept(metrics, MINER, solo_forced=True, is_block=True)
    assert _credit(metrics, MINER) == 900_000_000


@pytest.mark.asyncio
async def test_non_solo_share_unaffected(tmp_path):
    # A normal (non-solo) share on a pps pool with 30% ENA still skims to treasury.
    metrics = _metrics(tmp_path, fee_bps=3000, mode="pps", solo_fee_bps=500)
    await _accept(metrics, MINER, solo_forced=False, is_block=True)
    assert _credit(metrics, MINER) == 700_000_000
    assert _credit(metrics, TREASURY) == 300_000_000
