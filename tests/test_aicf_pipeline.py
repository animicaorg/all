"""Tests for the AICF pipeline-parallel + race-replication paths.

These exercise the protocol semantics directly through the
``rpc.methods.aicf_jobs`` RPC handlers, so the tests survive renames of
the underlying store types and cover the SQLite + in-memory paths
through the same handler entry points the chat client uses.

Pipeline coverage:
  - submit auto-promotes to pipeline when enough pipeline-tier workers
    are registered, falls back to race when not
  - K stages are claimed in lowest-index-first order and a single
    worker cannot hold two stages of the same job (deadlock prevention)
  - pipelineGetUpstreamActivation long-polls and reports correctly
    when upstream completes mid-poll
  - workerSubmitResult on a pipeline job is rejected with a clear
    ``use_pipeline_methods`` hint so legacy miners don't bypass stage
    chaining
  - the final-stage worker is credited; intermediate stages are not

Race coverage (parity smoke):
  - K=3 workers race the same job, first valid submit wins, losers
    receive lost_race and are not credited
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest


def _import_module(force_memory: bool, db_path: str | None = None):
    """Force-reload the module so per-test env overrides are honoured.

    The module reads ANIMICA_AICF_JOB_STORE / ANIMICA_AICF_JOB_DB at
    import time when it chooses an in-memory vs SQLite store. To run
    both backends in the same pytest run we re-import with the env
    set up the way we want."""
    if force_memory:
        os.environ["ANIMICA_AICF_JOB_STORE"] = "memory"
        os.environ.pop("ANIMICA_AICF_JOB_DB", None)
    else:
        os.environ.pop("ANIMICA_AICF_JOB_STORE", None)
        if db_path:
            os.environ["ANIMICA_AICF_JOB_DB"] = db_path
    # Disable the stub-fallback grace so pipeline jobs don't get
    # stubbed mid-test; we drive completion explicitly.
    os.environ["ANIMICA_AICF_WORKER_CLAIM_GRACE_S"] = "999"
    import importlib
    import sys
    for mod in [m for m in list(sys.modules) if m.startswith("rpc.methods.aicf_jobs")]:
        del sys.modules[mod]
    return importlib.import_module("rpc.methods.aicf_jobs")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------- #
# Pipeline                                                                    #
# --------------------------------------------------------------------------- #


def test_pipeline_promotes_when_workers_available():
    m = _import_module(force_memory=True)

    async def go():
        await m.worker_register(address="pw0", tiers=["pipeline"], hardware={})
        await m.worker_register(address="pw1", tiers=["pipeline"], hardware={})
        sub = await m.submit_inference_job(
            spec={"prompt": "q", "stages": 2},
            payment={"txn_hex": ""},
        )
        assert sub["mode"] == "pipeline", sub
        assert sub["stages"] == 2

    _run(go())


def test_pipeline_falls_back_to_race_without_workers():
    m = _import_module(force_memory=True)

    async def go():
        # No pipeline workers registered.
        await m.worker_register(address="rw0", tiers=["standard"], hardware={})
        sub = await m.submit_inference_job(
            spec={"prompt": "q"},
            payment={"txn_hex": ""},
        )
        assert sub["mode"] == "race", sub
        assert sub["stages"] == 0

    _run(go())


def test_pipeline_forced_mode_overrides_worker_check():
    m = _import_module(force_memory=True)

    async def go():
        # No pipeline workers, but caller forces pipeline mode.
        sub = await m.submit_inference_job(
            spec={"prompt": "q", "mode": "pipeline", "stages": 3},
            payment={"txn_hex": ""},
        )
        assert sub["mode"] == "pipeline"
        assert sub["stages"] == 3

    _run(go())


def test_pipeline_stage_claim_lowest_first_and_no_double_claim():
    m = _import_module(force_memory=True)

    async def go():
        await m.worker_register(address="pw0", tiers=["pipeline"], hardware={})
        await m.worker_register(address="pw1", tiers=["pipeline"], hardware={})
        sub = await m.submit_inference_job(
            spec={"prompt": "q", "stages": 2},
            payment={"txn_hex": ""},
        )
        s0 = await m.pipeline_claim_stage(address="pw0")
        assert s0["stage_index"] == 0
        s1 = await m.pipeline_claim_stage(address="pw1")
        assert s1["stage_index"] == 1
        # pw0 cannot claim a second stage of the SAME job — would
        # deadlock waiting on its own upstream.
        again = await m.pipeline_claim_stage(address="pw0", job_id=sub["job_id"])
        assert again is None

    _run(go())


def test_pipeline_upstream_blocks_then_completes():
    m = _import_module(force_memory=True)

    async def go():
        await m.worker_register(address="pw0", tiers=["pipeline"], hardware={})
        await m.worker_register(address="pw1", tiers=["pipeline"], hardware={})
        sub = await m.submit_inference_job(
            spec={"prompt": "q", "stages": 2},
            payment={"txn_hex": ""},
        )
        await m.pipeline_claim_stage(address="pw0")
        await m.pipeline_claim_stage(address="pw1")

        # Stage 1 upstream poll before stage 0 submits → ready=False.
        early = await m.pipeline_get_upstream_activation(
            job_id=sub["job_id"], stage_index=1,
        )
        assert early["ready"] is False

        # Stage 0 submits → stage 1's next poll is ready.
        await m.pipeline_submit_stage_result(
            address="pw0", job_id=sub["job_id"], stage_index=0,
            output_b64="aGVsbG8=", output_text=None, meta={},
        )
        ready = await m.pipeline_get_upstream_activation(
            job_id=sub["job_id"], stage_index=1,
        )
        assert ready["ready"] is True
        assert ready["output_b64"] == "aGVsbG8="

    _run(go())


def test_pipeline_final_stage_credits_only_one_worker():
    m = _import_module(force_memory=True)

    async def go():
        await m.worker_register(address="pw0", tiers=["pipeline"], hardware={})
        await m.worker_register(address="pw1", tiers=["pipeline"], hardware={})
        sub = await m.submit_inference_job(
            spec={"prompt": "q", "stages": 2},
            payment={"txn_hex": ""},
        )
        jid = sub["job_id"]
        await m.pipeline_claim_stage(address="pw0")
        await m.pipeline_claim_stage(address="pw1")
        await m.pipeline_submit_stage_result(
            address="pw0", job_id=jid, stage_index=0,
            output_b64="aA==", output_text=None, meta={},
        )
        final = await m.pipeline_submit_stage_result(
            address="pw1", job_id=jid, stage_index=1,
            output_b64=None, output_text="composed answer", meta={},
        )
        assert final["status"] == "completed_job"
        e0 = await m.worker_earnings(address="pw0")
        e1 = await m.worker_earnings(address="pw1")
        assert e0["jobs_completed"] == 0, e0
        assert e1["jobs_completed"] == 1, e1
        assert e1["earnings_pending_animica"] > 0

        settle = await m.settle_job(job_id=jid)
        assert settle["text"] == "composed answer"
        assert settle["provider_id"] == "pw1"

    _run(go())


def test_worker_submit_result_rejects_pipeline_jobs():
    m = _import_module(force_memory=True)

    async def go():
        await m.worker_register(address="pw0", tiers=["pipeline"], hardware={})
        await m.worker_register(address="pw1", tiers=["pipeline"], hardware={})
        sub = await m.submit_inference_job(
            spec={"prompt": "q", "stages": 2},
            payment={"txn_hex": ""},
        )
        rej = await m.worker_submit_result(
            address="pw0", job_id=sub["job_id"], text="x",
            latency_ms=1, attestation={},
        )
        assert rej["accepted"] is False
        assert rej["reason"] == "use_pipeline_methods"

    _run(go())


def test_pipeline_works_against_sqlite_store():
    db_path = tempfile.mktemp(suffix=".db")
    m = _import_module(force_memory=False, db_path=db_path)

    async def go():
        await m.worker_register(address="pw0", tiers=["pipeline"], hardware={})
        await m.worker_register(address="pw1", tiers=["pipeline"], hardware={})
        sub = await m.submit_inference_job(
            spec={"prompt": "q", "stages": 2},
            payment={"txn_hex": ""},
        )
        assert sub["mode"] == "pipeline"
        jid = sub["job_id"]
        s0 = await m.pipeline_claim_stage(address="pw0")
        s1 = await m.pipeline_claim_stage(address="pw1")
        assert {s0["stage_index"], s1["stage_index"]} == {0, 1}
        await m.pipeline_submit_stage_result(
            address="pw0", job_id=jid, stage_index=0,
            output_b64="ZGF0YQ==", output_text=None, meta={},
        )
        await m.pipeline_submit_stage_result(
            address="pw1", job_id=jid, stage_index=1,
            output_b64=None, output_text="sqlite-final", meta={},
        )
        settle = await m.settle_job(job_id=jid)
        assert settle["text"] == "sqlite-final"
        assert settle["provider_id"] == "pw1"

    try:
        _run(go())
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Race                                                                        #
# --------------------------------------------------------------------------- #


def test_race_first_writer_wins_only_winner_credited():
    m = _import_module(force_memory=True)

    async def go():
        await m.worker_register(address="wA", tiers=["standard"], hardware={})
        await m.worker_register(address="wB", tiers=["standard"], hardware={})
        await m.worker_register(address="wC", tiers=["standard"], hardware={})
        sub = await m.submit_inference_job(
            spec={"prompt": "q", "tier_preferred": "standard"},
            payment={"txn_hex": ""},
        )
        assert sub["mode"] == "race"
        jid = sub["job_id"]

        # Three workers race
        c1 = await m.worker_claim_next_job(address="wA", tiers=["standard"])
        c2 = await m.worker_claim_next_job(address="wB", tiers=["standard"])
        c3 = await m.worker_claim_next_job(address="wC", tiers=["standard"])
        assert all(c is not None for c in (c1, c2, c3))
        # Beyond K=3 is capped
        c4 = await m.worker_claim_next_job(address="wD", tiers=["standard"])
        assert c4 is None

        # wB wins
        r_win = await m.worker_submit_result(
            address="wB", job_id=jid, text="B-text",
            latency_ms=1, attestation={},
        )
        assert r_win["accepted"] is True
        # wA loses
        r_lose = await m.worker_submit_result(
            address="wA", job_id=jid, text="A-text",
            latency_ms=1, attestation={},
        )
        assert r_lose["accepted"] is False
        assert r_lose["reason"] == "lost_race"
        assert r_lose["winner_address"] == "wB"

        # Earnings reflect winner only
        eA = await m.worker_earnings(address="wA")
        eB = await m.worker_earnings(address="wB")
        assert eA["jobs_completed"] == 0
        assert eB["jobs_completed"] == 1

    _run(go())


if __name__ == "__main__":     # pragma: no cover — manual smoke
    pytest.main([__file__, "-v"])
