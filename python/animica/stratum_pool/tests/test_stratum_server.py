import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.core import MiningJob
from animica.stratum_pool.job_manager import JobManager
from animica.stratum_pool.stratum_server import PoolShareValidator, StratumPoolServer


class DummyAdapter:
    pass


@pytest.mark.asyncio
async def test_on_new_job_updates_theta_and_broadcasts_difficulty():
    # The pool anchors the FIRST job at start_difficulty (0.3 here), NOT the
    # template's block-difficulty share_target (0.5) — see _resolve_share_target.
    # floor disabled so start-difficulty anchoring is exercised in isolation (the
    # xmrig-compat floor is covered by test_vardiff_difficulty_floor.py).
    cfg = PoolConfig(start_difficulty=0.3, share_difficulty_floor=0.0)
    server = StratumPoolServer(DummyAdapter(), cfg, JobManager(DummyAdapter(), cfg))
    server.stratum.set_global_difficulty = AsyncMock()
    server.stratum.publish_job = AsyncMock()

    job = MiningJob(
        job_id="job-1",
        header={"signBytes": "0x1234"},
        theta_micro=1_000_000,
        share_target=0.5,
        height=7,
        target="0x99",
        sign_bytes="0x1234",
        hints={"mixSeed": "0x55"},
        raw={"templateId": "job-1", "header": {"signBytes": "0x1234"}},
    )

    await server._on_new_job(job)

    # emitted difficulty == start_difficulty (0.3), not the template's 0.5
    server.stratum.set_global_difficulty.assert_awaited_once_with(0.3, 1_000_000)
    published_job = server.stratum.publish_job.await_args.args[0]
    assert published_job.header["thetaMicro"] == 1_000_000
    assert published_job.header["thetaTargetMicro"] == 1_000_000
    assert published_job.header["theta_target_micro"] == 1_000_000
    assert published_job.raw["templateId"] == "job-1"
    assert published_job.raw["_sourceJobId"] == "job-1"
    assert published_job.raw["_validationFingerprint"]
    assert published_job.job_id.startswith("job-1-")


@pytest.mark.asyncio
async def test_on_new_job_overrides_stale_header_theta_with_issued_theta():
    server = StratumPoolServer(
        DummyAdapter(), PoolConfig(), JobManager(DummyAdapter(), PoolConfig())
    )
    server.stratum.set_global_difficulty = AsyncMock()
    server.stratum.publish_job = AsyncMock()

    job = MiningJob(
        job_id="job-theta-override",
        header={
            "signBytes": "0x1234",
            "thetaMicro": 900_000,
            "thetaTargetMicro": 900_000,
            "theta_target_micro": 900_000,
        },
        theta_micro=1_200_000,
        issued_theta_micro=1_200_000,
        share_target=0.5,
        height=7,
        target="0x99",
        sign_bytes="0x1234",
        hints={"mixSeed": "0x55"},
        raw={"templateId": "job-theta-override"},
    )

    await server._on_new_job(job)

    published_job = server.stratum.publish_job.await_args.args[0]
    assert int(published_job.theta_micro) == 1_200_000
    assert int(published_job.header["thetaMicro"]) == 1_200_000
    assert int(published_job.header["thetaTargetMicro"]) == 1_200_000
    assert int(published_job.header["theta_target_micro"]) == 1_200_000


@pytest.mark.asyncio
async def test_on_new_job_clamps_theta_micro_difficulty_bounds():
    # start_difficulty (0.9 → 900_000 θµ) sits ABOVE the max bound, so the emitted
    # threshold clamps DOWN to the upper bound (240_000 θµ → ratio 0.24). floor
    # disabled to isolate the θµ clamp (floor covered by test_vardiff_difficulty_floor).
    cfg = PoolConfig(
        min_difficulty=120_000,
        max_difficulty=240_000,
        start_difficulty=0.9,
        share_difficulty_floor=0.0,
    )
    server = StratumPoolServer(
        DummyAdapter(), cfg, JobManager(DummyAdapter(), cfg)
    )
    server.stratum.set_global_difficulty = AsyncMock()
    server.stratum.publish_job = AsyncMock()

    job = MiningJob(
        job_id="job-micro",
        header={"signBytes": "0x1234"},
        theta_micro=1_000_000,
        share_target=1.0,
        height=7,
        target="0x99",
        sign_bytes="0x1234",
        hints={"mixSeed": "0x55"},
        raw={"templateId": "job-micro", "header": {"signBytes": "0x1234"}},
    )

    await server._on_new_job(job)

    server.stratum.set_global_difficulty.assert_awaited_once_with(0.24, 1_000_000)
    published_job = server.stratum.publish_job.await_args.args[0]
    assert published_job.share_target == 0.24
    assert int(published_job.raw["_shareThresholdMicro"]) == 240_000


@pytest.mark.asyncio
async def test_on_new_job_uses_min_difficulty_when_template_omits_share_target():
    cfg = PoolConfig(min_difficulty=0.02, max_difficulty=1.0)
    server = StratumPoolServer(
        DummyAdapter(), cfg, JobManager(DummyAdapter(), cfg)
    )
    server.stratum.set_global_difficulty = AsyncMock()
    server.stratum.publish_job = AsyncMock()

    job = MiningJob(
        job_id="job-no-share-target",
        header={"signBytes": "0x1234"},
        theta_micro=1_000_000,
        share_target=1.0,
        height=7,
        target="0x99",
        sign_bytes="0x1234",
        hints={"mixSeed": "0x55"},
        raw={
            "templateId": "job-no-share-target",
            "header": {"signBytes": "0x1234"},
            "_shareTargetProvided": False,
        },
    )

    await server._on_new_job(job)

    # xmrig-compat difficulty floor (share_difficulty_floor=1.0) overrides a
    # min_difficulty that is EASIER than the floor. At this test's degenerate
    # θ=1e6 (1 nat) the block difficulty itself is ~6e-10 — far below diff-1 —
    # so a floor (diff-1) share would be HARDER than a block; the floor is then
    # clamped to the block target and the easiest share IS the block (ratio 1.0).
    # min_difficulty=0.02 (→ 20_000 µ-nats) can no longer take effect here. The
    # realistic θ case (floor lands between min_difficulty and the block) is
    # covered by test_vardiff_difficulty_floor.py.
    server.stratum.set_global_difficulty.assert_awaited_once_with(1.0, 1_000_000)
    published_job = server.stratum.publish_job.await_args.args[0]
    assert published_job.share_target == pytest.approx(1.0)
    assert int(published_job.raw["_shareThresholdMicro"]) == 1_000_000


@pytest.mark.asyncio
async def test_share_target_auto_adjusts_down_and_up():
    # start_difficulty (0.4) anchors the first job mid-band [100_000, 900_000] so
    # vardiff has room to step DOWN (on low-diff rejects) then UP (on accepts).
    # floor disabled to isolate the auto-adjust algorithm (floor covered by
    # test_vardiff_difficulty_floor.py).
    cfg = PoolConfig(
        min_difficulty=100_000,
        max_difficulty=900_000,
        start_difficulty=0.4,
        share_difficulty_floor=0.0,
    )
    server = StratumPoolServer(
        DummyAdapter(), cfg, JobManager(DummyAdapter(), cfg)
    )
    server.stratum.set_global_difficulty = AsyncMock()
    server.stratum.publish_job = AsyncMock()

    job = MiningJob(
        job_id="job-vardiff",
        header={"signBytes": "0x1234"},
        theta_micro=1_000_000,
        share_target=0.4,
        height=7,
        target="0x99",
        sign_bytes="0x1234",
        hints={"mixSeed": "0x55"},
        raw={"templateId": "job-vardiff", "header": {"signBytes": "0x1234"}},
    )
    await server._on_new_job(job)

    current_job = server._current_stratum_job
    assert current_job is not None
    base_ratio = float(current_job.share_target)

    for _ in range(8):
        await server._handle_share_submit(
            object(),
            current_job,
            {},
            False,
            "low difficulty share",
            False,
            0,
        )

    lowered_ratio = float(server._current_stratum_job.share_target)  # type: ignore[union-attr]
    assert lowered_ratio < base_ratio

    server._last_vardiff_adjust_ts = 0.0
    current_job = server._current_stratum_job
    assert current_job is not None
    for _ in range(8):
        await server._handle_share_submit(
            object(),
            current_job,
            {},
            True,
            None,
            False,
            0,
        )

    raised_ratio = float(server._current_stratum_job.share_target)  # type: ignore[union-attr]
    assert raised_ratio > lowered_ratio
    assert raised_ratio <= 0.9


@pytest.mark.asyncio
async def test_pool_disables_unfloored_session_vardiff():
    # The pool must run ONLY the floored pool vardiff. StratumServer has its OWN
    # per-session vardiff whose bounds derive from start_difficulty (not
    # share_difficulty_floor) and whose _push_difficulty bypasses the wire floor.
    # Once the min-difficulty pin is lifted, that second loop could emit a sub-1.0
    # set_difficulty (down to start*0.05 ≈ 5e-4 ratio) to a v1/xmrig-style
    # Animica-native session and trigger the connect→set_difficulty→disconnect
    # loop. Guard against a regression that re-arms it.
    cfg = PoolConfig()
    server = StratumPoolServer(DummyAdapter(), cfg, JobManager(DummyAdapter(), cfg))
    assert server._server._session_vardiff_enabled is False


@pytest.mark.asyncio
async def test_on_new_job_same_source_id_with_new_binding_gets_new_effective_job_id():
    server = StratumPoolServer(
        DummyAdapter(),
        PoolConfig(),
        JobManager(DummyAdapter(), PoolConfig()),
    )
    server.stratum.set_global_difficulty = AsyncMock()
    server.stratum.publish_job = AsyncMock()

    base_header = {"signBytes": "0x1234", "thetaMicro": 1_000_000}
    first = MiningJob(
        job_id="job-stable",
        source_job_id="job-stable",
        header=dict(base_header, timestamp=1),
        theta_micro=1_000_000,
        share_target=0.5,
        height=7,
        target="0x99",
        sign_bytes="0x1234",
        raw={
            "templateId": "job-stable",
            "header": dict(base_header, timestamp=1),
            "target": "0x99",
        },
    )
    second = MiningJob(
        job_id="job-stable",
        source_job_id="job-stable",
        header=dict(base_header, timestamp=2),
        theta_micro=1_000_000,
        share_target=0.5,
        height=7,
        target="0x98",
        sign_bytes="0x1234",
        raw={
            "templateId": "job-stable",
            "header": dict(base_header, timestamp=2),
            "target": "0x98",
        },
    )

    await server._on_new_job(first)
    await server._on_new_job(second)

    assert server.stratum.publish_job.await_count == 2
    first_publish = server.stratum.publish_job.await_args_list[0]
    second_publish = server.stratum.publish_job.await_args_list[1]
    first_job = first_publish.args[0]
    second_job = second_publish.args[0]
    assert first_job.job_id != second_job.job_id
    assert first_job.job_id.startswith("job-stable-")
    assert second_job.job_id.startswith("job-stable-")
    assert first_publish.kwargs["clean_jobs"] is True
    assert second_publish.kwargs["clean_jobs"] is True


@pytest.mark.asyncio
async def test_pool_share_validator_preserves_template_raw():
    class CapturingAdapter:
        def __init__(self) -> None:
            self.seen_job = None

        async def validate_and_submit_share(self, job, submit_params):
            self.seen_job = job
            return True, None, False, 0

    adapter = CapturingAdapter()
    validator = PoolShareValidator(adapter)

    from mining.stratum_server import StratumJob

    raw_template = {
        "templateId": "job-1",
        "header": {"parentHash": "0x" + "11" * 32, "height": 7},
        "target": "0x" + "ff" * 32,
        "parent": {"height": 6, "hash": "0x" + "11" * 32},
        "txs": [],
    }
    stratum_job = StratumJob(
        job_id="job-1",
        header={"number": 7, "signBytes": "0x1234"},
        share_target=1.0,
        theta_micro=1_000_000,
        target="0x" + "ff" * 32,
        sign_bytes="0x1234",
        height=7,
        raw=raw_template,
    )

    accepted, reason, is_block, tx_count = await validator.validate(
        stratum_job,
        {
            "hashshare": {"nonce": "0x01", "body": {}},
            "_address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
            "_worker": "worker-1",
        },
    )

    assert accepted is True
    assert reason is None
    assert is_block is False
    assert tx_count == 0
    assert adapter.seen_job is not None
    assert adapter.seen_job.height == 7
    assert adapter.seen_job.raw["templateId"] == raw_template["templateId"]
    assert adapter.seen_job.raw["target"] == raw_template["target"]
    assert adapter.seen_job.raw["parent"] == raw_template["parent"]
    assert adapter.seen_job.raw["txs"] == raw_template["txs"]
    assert adapter.seen_job.raw["_sourceJobId"] == "job-1"


@pytest.mark.asyncio
async def test_pool_share_validator_rejects_missing_address():
    class CapturingAdapter:
        async def validate_and_submit_share(self, job, submit_params):
            return True, None, False, 0

    from mining.stratum_server import StratumJob

    validator = PoolShareValidator(CapturingAdapter(), pool_mode="pps")
    accepted, reason, _is_block, _tx_count = await validator.validate(
        StratumJob(job_id="j1", header={}, share_target=1.0, theta_micro=1),
        {"hashshare": {"nonce": "0x01", "body": {}}},
    )
    assert accepted is False
    assert reason == "missing miner payout address"


@pytest.mark.asyncio
async def test_pool_share_validator_solo_mode_accepts_multiple_addresses():
    class CapturingAdapter:
        async def validate_and_submit_share(self, job, submit_params):
            return True, None, False, 0

    from mining.stratum_server import StratumJob

    validator = PoolShareValidator(CapturingAdapter(), pool_mode="solo")
    job = StratumJob(job_id="j1", header={}, share_target=1.0, theta_micro=1)

    first = await validator.validate(
        job,
        {
            "hashshare": {"nonce": "0x01", "body": {}},
            "_address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
        },
    )
    second = await validator.validate(
        job,
        {
            "hashshare": {"nonce": "0x02", "body": {}},
            "_address": "anim1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        },
    )
    assert first[0] is True
    assert second[0] is True
