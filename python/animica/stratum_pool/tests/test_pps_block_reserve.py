"""PPS block-reserve: with the credit cap active, the block-winning share must
not drain the entire headroom its own block minted — a slice is held back so
miners who never find a block still earn per-share PPS credit.

Live failure this guards against (2026-08-06): cap_remaining sat pinned at 0,
the dominant miner's winning shares (assigned ratio 1.0 -> priced at full
reward) consumed every block's fresh headroom instantly, and every other
miner's shares were clamped to zero credit — PPS silently degenerated into
"paid only if you find the block"."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.metrics import PoolMetrics
from mining.stratum_server import Session, StratumJob

WINNER = "anim1blockwinnerxxxxxxxxxxxxxxxxxxxxxxxxxx"
SMALL = "anim1smallminerxxxxxxxxxxxxxxxxxxxxxxxxxxx"
REWARD = 1_000_000_000  # 1 ANM in nano


class DummyJobManager:
    def request_refresh(self) -> None:
        pass


class DummyServer:
    def stats(self):
        return {}

    def session_snapshots(self):
        return []


def _metrics(tmp_path, *, reserve_bps: int) -> PoolMetrics:
    db = tmp_path / "pool.db"
    return PoolMetrics(
        PoolConfig(
            db_url=f"sqlite:///{db}",
            pool_mode="pps",
            credit_cap_enabled=True,
            pps_block_reserve_bps=reserve_bps,
        ),
        DummyJobManager(),
        DummyServer(),
    )


def _job(job_id: str = "job-1", height: int = 7) -> StratumJob:
    # Distinct job_id per block matters: blocks are keyed by job_id, so a
    # reused id REPLACES the prior block row instead of adding mined coinbase.
    return StratumJob(
        job_id=job_id,
        header={"number": height},
        share_target=1.0,
        theta_micro=1_000_000,
        raw={"coinbase": {"amount": REWARD}},
    )


async def _accept(
    metrics: PoolMetrics,
    address: str,
    *,
    is_block: bool,
    d_ratio: float = 1.0,
    job_id: str = "job-1",
    height: int = 7,
) -> None:
    session = Session(session_id="s1", writer=None, worker="rig", address=address)
    await metrics.record_share(
        session,
        _job(job_id, height),
        submit_params={"d_ratio": d_ratio},
        ok=True,
        reason=None,
        is_block=is_block,
        tx_count=0,
    )
    metrics._run_housekeeping(force=True)


def _credit(metrics: PoolMetrics, address: str) -> int:
    with metrics._db_lock:
        row = metrics._db.execute(
            "SELECT COALESCE(SUM(total_credit),0) FROM worker_balances WHERE address=?",
            (address,),
        ).fetchone()
    return int(row[0] or 0)


@pytest.mark.asyncio
async def test_non_finder_earns_after_winner_block(tmp_path):
    # The user-visible requirement: a miner who never finds a block still gets
    # PPS credit for shares submitted after someone else's block.
    metrics = _metrics(tmp_path, reserve_bps=500)
    await _accept(metrics, WINNER, is_block=True)
    winner = _credit(metrics, WINNER)
    assert winner == (REWARD * 9_500) // 10_000  # 95% of the fresh headroom

    await _accept(metrics, SMALL, is_block=False)
    small = _credit(metrics, SMALL)
    assert small > 0
    assert winner + small <= REWARD  # still fully backed by mined coinbase


@pytest.mark.asyncio
async def test_reserve_zero_reproduces_finder_takes_all(tmp_path):
    # reserve_bps=0 is the pre-fix behaviour: winner drains the headroom and
    # the non-finder's share clamps to zero. Kept as a pinned regression oracle
    # so the reserve's effect stays observable.
    metrics = _metrics(tmp_path, reserve_bps=0)
    await _accept(metrics, WINNER, is_block=True)
    await _accept(metrics, SMALL, is_block=False)
    assert _credit(metrics, WINNER) == REWARD
    assert _credit(metrics, SMALL) == 0
    assert metrics._credit_clamped_total > 0


@pytest.mark.asyncio
async def test_unconsumed_reserve_returns_to_winner(tmp_path):
    # The holdback is a buffer, not a fee: with no small-share demand, leftover
    # reserve carries forward and the winner's next block credit exceeds the
    # per-block 95% slice.
    metrics = _metrics(tmp_path, reserve_bps=500)
    await _accept(metrics, WINNER, is_block=True, job_id="job-1", height=7)
    first = _credit(metrics, WINNER)
    await _accept(metrics, WINNER, is_block=True, job_id="job-2", height=8)
    second = _credit(metrics, WINNER) - first
    assert second > first
    assert _credit(metrics, WINNER) <= 2 * REWARD


@pytest.mark.asyncio
async def test_solo_block_credit_unaffected_by_reserve(tmp_path):
    db = tmp_path / "solo.db"
    metrics = PoolMetrics(
        PoolConfig(
            db_url=f"sqlite:///{db}",
            pool_mode="solo",
            credit_cap_enabled=True,
            pps_block_reserve_bps=500,
        ),
        DummyJobManager(),
        DummyServer(),
    )
    await _accept(metrics, WINNER, is_block=True)
    assert _credit(metrics, WINNER) == REWARD


@pytest.mark.asyncio
async def test_clamped_credit_is_deferred_then_paid(tmp_path):
    """Cap headroom arrives in block-sized lumps while credit is issued
    continuously, so a share landing at the wrong moment used to be paid nothing
    — permanently, and hardest on sub-block miners who submit most often. The
    shortfall must be remembered and paid from later headroom."""
    # reserve_bps=500 is the shipped default: the block winner leaves a slice of
    # its own block's headroom for the shares that arrive between blocks.
    metrics = _metrics(tmp_path, reserve_bps=500)
    # No mined coinbase yet => zero headroom => this share can pay nothing.
    await _accept(metrics, SMALL, is_block=False, d_ratio=0.5, job_id="s-1")
    assert _credit(metrics, SMALL) == 0
    owed = metrics._deferred_credit("pps", "rig", SMALL)
    assert owed > 0, "shortfall was destroyed instead of deferred"

    # A block mints headroom; the reserve leaves some of it unspent, and the
    # worker's next share settles part of the debt out of it.
    await _accept(metrics, WINNER, is_block=True, job_id="b-1", height=8)
    await _accept(metrics, SMALL, is_block=False, d_ratio=0.5, job_id="s-2")
    paid = _credit(metrics, SMALL)
    assert paid > 0, "deferred credit was never flushed"
    assert paid <= owed, "flushed more than was owed"
    # Still bounded by what the pool actually mined.
    assert _credit(metrics, SMALL) + _credit(metrics, WINNER) <= REWARD


@pytest.mark.asyncio
async def test_deferred_credit_is_not_payable(tmp_path):
    # Deferred credit must never look like a balance the pool owes now, or the
    # cap's whole purpose (never credit more than mined) is defeated.
    metrics = _metrics(tmp_path, reserve_bps=0)
    await _accept(metrics, SMALL, is_block=False, d_ratio=0.5, job_id="s-1")
    assert metrics._deferred_credit("pps", "rig", SMALL) > 0
    with metrics._db_lock:
        row = metrics._db.execute(
            "SELECT COALESCE(SUM(total_credit),0) FROM worker_balances"
        ).fetchone()
    assert int(row[0] or 0) == 0
