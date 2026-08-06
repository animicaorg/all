"""Credit cap: when enabled, total credited balance may not outpace actual
mined coinbase. A deploy-time baseline (mined_base/credited_base) excludes any
pre-existing credited>mined overhang, so the cap only holds NEW credit to NEW
mined coinbase — it never freezes ongoing earnings to repay history."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.metrics import PoolMetrics
from mining.stratum_server import Session, StratumJob

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


def _metrics(
    tmp_path,
    *,
    mode: str = "pps",
    cap: bool = False,
    mined_base: int = 0,
    credited_base: int = 0,
) -> PoolMetrics:
    db = tmp_path / "pool.db"
    return PoolMetrics(
        PoolConfig(
            db_url=f"sqlite:///{db}",
            pool_mode=mode,
            credit_cap_enabled=cap,
            credit_cap_mined_base=mined_base,
            credit_cap_credited_base=credited_base,
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


async def _accept(metrics: PoolMetrics, address: str, *, is_block: bool = False) -> None:
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
async def test_cap_disabled_credits_unbacked_share_fully(tmp_path):
    # Legacy behaviour: with the cap off, a non-block PPS share still credits the
    # full reward even though no coinbase was mined for it.
    metrics = _metrics(tmp_path, cap=False)
    await _accept(metrics, MINER)
    assert _credit(metrics, MINER) == REWARD


@pytest.mark.asyncio
async def test_cap_clamps_unbacked_credit_to_zero(tmp_path):
    # Cap on, nothing mined yet -> budget 0 -> a non-block PPS share credits zero.
    metrics = _metrics(tmp_path, cap=True)
    await _accept(metrics, MINER)
    assert _credit(metrics, MINER) == 0


@pytest.mark.asyncio
async def test_cap_allows_block_backed_credit(tmp_path):
    # A real block records mined coinbase before crediting, so its credit fits.
    metrics = _metrics(tmp_path, mode="solo", cap=True)
    await _accept(metrics, MINER, is_block=True)
    assert _credit(metrics, MINER) == REWARD


@pytest.mark.asyncio
async def test_cap_clamps_credit_beyond_mined(tmp_path):
    # One block mined (REWARD), but PPS shares try to credit two rewards.
    # The first (block-backed) is fully credited; the second is clamped to the
    # remaining headroom (zero) because no further coinbase was mined.
    metrics = _metrics(tmp_path, mode="pps", cap=True)
    await _accept(metrics, MINER, is_block=True)   # mined += REWARD, credit REWARD
    await _accept(metrics, MINER, is_block=False)  # no new mined -> clamped to 0
    assert _credit(metrics, MINER) == REWARD


@pytest.mark.asyncio
async def test_headroom_cache_is_exact_across_many_shares(tmp_path):
    """The cap sums the whole blocks table per accepted share (~12ms at 45k
    rows), which the sub-block share rate (~2/s) makes too expensive. The cache
    must be EXACT, not approximate: credit spent against a snapshot is tracked
    locally, and a newly mined block invalidates it so fresh coinbase is not
    withheld. Cached and uncached runs must agree exactly."""
    def run(ttl: str):
        import os
        os.environ["ANIMICA_POOL_CREDIT_CAP_CACHE_TTL"] = ttl
        db = tmp_path / f"cap-{ttl}.db"
        return PoolMetrics(
            PoolConfig(
                db_url=f"sqlite:///{db}",
                pool_mode="pps",
                credit_cap_enabled=True,
                pps_block_reserve_bps=0,
            ),
            DummyJobManager(),
            DummyServer(),
        )

    async def drive(metrics):
        # One block (mints headroom) then many sub-block shares against it.
        session = Session(session_id="s1", writer=None, worker=WORKER, address=MINER)
        job = StratumJob(
            job_id="block-job",
            header={"number": 7},
            share_target=1.0,
            theta_micro=1_000_000,
            raw={"coinbase": {"amount": REWARD}},
        )
        await metrics.record_share(
            session, job, submit_params={"d_ratio": 1.0}, ok=True, reason=None,
            is_block=True, tx_count=0,
        )
        for i in range(25):
            sub = StratumJob(
                job_id=f"share-job-{i}",
                header={"number": 8},
                share_target=0.5,
                theta_micro=1_000_000,
                raw={"coinbase": {"amount": REWARD}},
            )
            await metrics.record_share(
                session, sub, submit_params={"d_ratio": 0.5}, ok=True, reason=None,
                is_block=False, tx_count=0,
            )
        metrics._run_housekeeping(force=True)
        return _credit(metrics, MINER)

    try:
        cached = await drive(run("30"))
        uncached = await drive(run("0"))
        assert cached == uncached, f"cache changed the outcome: {cached} != {uncached}"
        # And credit stayed backed by the single block's coinbase.
        assert cached <= REWARD
    finally:
        import os
        os.environ.pop("ANIMICA_POOL_CREDIT_CAP_CACHE_TTL", None)


@pytest.mark.asyncio
async def test_fresh_block_headroom_not_withheld_by_cache(tmp_path):
    # A block mined while a snapshot is live must become spendable immediately —
    # otherwise miners' shares clamp to zero for the whole TTL, which is the
    # very failure the reserve/sub-block work exists to remove.
    import os
    os.environ["ANIMICA_POOL_CREDIT_CAP_CACHE_TTL"] = "3600"
    try:
        metrics = _metrics(tmp_path, mode="pps", cap=True)
        session = Session(session_id="s1", writer=None, worker=WORKER, address=MINER)
        # Prime the snapshot at zero headroom.
        assert metrics._credit_cap_remaining() == 0
        job = StratumJob(
            job_id="fresh-block",
            header={"number": 9},
            share_target=1.0,
            theta_micro=1_000_000,
            raw={"coinbase": {"amount": REWARD}},
        )
        await metrics.record_share(
            session, job, submit_params={"d_ratio": 1.0}, ok=True, reason=None,
            is_block=True, tx_count=0,
        )
        metrics._run_housekeeping(force=True)
        # Immediately spendable: the block share takes everything except the
        # deliberate 5% pps_block_reserve holdback (kept for other miners'
        # sub-block shares). A cache that withheld the fresh coinbase would
        # credit 0 here.
        assert _credit(metrics, MINER) == (REWARD * 9_500) // 10_000
    finally:
        os.environ.pop("ANIMICA_POOL_CREDIT_CAP_CACHE_TTL", None)


@pytest.mark.asyncio
async def test_cap_baseline_excludes_historical_overhang(tmp_path):
    # Simulate a pre-existing credited>mined overhang via the baseline: credited
    # is 10 rewards "ahead" at deploy. New block-backed credit must still flow —
    # the cap does not try to repay history.
    metrics = _metrics(
        tmp_path, mode="solo", cap=True, mined_base=0, credited_base=10 * REWARD
    )
    await _accept(metrics, MINER, is_block=True)
    assert _credit(metrics, MINER) == REWARD
