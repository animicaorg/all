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


def test_pipeline_final_stage_settles_job_and_records_text():
    """Final-stage submit closes the job, settleJob returns the
    final-stage text, provider_id reflects the final-stage worker.

    Earnings-credit semantics are covered separately in
    test_pipeline_split_credits_all_stage_workers_equally — that test
    asserts every completed stage worker is credited their share."""
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

        # Both workers were credited (multi-party split). Each gets
        # cost / 2. Total credited matches the original job cost.
        cost = float(sub["estimated_cost_animica"])
        e0 = await m.worker_earnings(address="pw0")
        e1 = await m.worker_earnings(address="pw1")
        assert e0["jobs_completed"] == 1
        assert e1["jobs_completed"] == 1
        assert abs(e0["earnings_pending_animica"] - cost / 2) < 1e-8
        assert abs(e1["earnings_pending_animica"] - cost / 2) < 1e-8

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


# --------------------------------------------------------------------------- #
# Multi-party revenue split                                                   #
# --------------------------------------------------------------------------- #


def test_pipeline_split_credits_all_stage_workers_equally():
    m = _import_module(force_memory=True)

    async def go():
        await m.worker_register(address="a", tiers=["pipeline"], hardware={})
        await m.worker_register(address="b", tiers=["pipeline"], hardware={})
        await m.worker_register(address="c", tiers=["pipeline"], hardware={})
        sub = await m.submit_inference_job(
            spec={"prompt": "q", "stages": 3},
            payment={"txn_hex": ""},
        )
        jid = sub["job_id"]
        cost = float(sub["estimated_cost_animica"])
        assert cost > 0.0
        s0 = await m.pipeline_claim_stage(address="a")
        s1 = await m.pipeline_claim_stage(address="b")
        s2 = await m.pipeline_claim_stage(address="c")
        for sub_stage, addr in (
            (s0, "a"), (s1, "b"),
        ):
            await m.pipeline_submit_stage_result(
                address=addr, job_id=jid,
                stage_index=sub_stage["stage_index"],
                output_b64="AA==", output_text=None, meta={},
            )
        # Final stage; payouts settle here.
        await m.pipeline_submit_stage_result(
            address="c", job_id=jid, stage_index=s2["stage_index"],
            output_b64=None, output_text="final", meta={},
        )
        ea = await m.worker_earnings(address="a")
        eb = await m.worker_earnings(address="b")
        ec = await m.worker_earnings(address="c")
        # All three credited
        assert ea["jobs_completed"] == 1
        assert eb["jobs_completed"] == 1
        assert ec["jobs_completed"] == 1
        # Equal share, within rounding (round_9)
        expected = cost / 3.0
        assert abs(ea["earnings_pending_animica"] - expected) < 1e-8
        assert abs(eb["earnings_pending_animica"] - expected) < 1e-8
        assert abs(ec["earnings_pending_animica"] - expected) < 1e-8
        # Total reflects the full cost (modulo 1e-8 rounding error)
        total = (ea["earnings_pending_animica"]
                 + eb["earnings_pending_animica"]
                 + ec["earnings_pending_animica"])
        assert abs(total - cost) < 1e-7, (total, cost)

    _run(go())


# --------------------------------------------------------------------------- #
# Direct worker-to-worker activation transport                                #
# --------------------------------------------------------------------------- #


def test_direct_transport_round_trip_with_signature():
    import sys
    sys.path.insert(0, "ai/agent_runtime/src")
    from agent_runtime.pipeline_transport import (
        PipelineTransportServer, compute_payload_tag, fetch_direct,
        verify_payload_tag,
    )

    secret = b"shared-test-secret"
    srv = PipelineTransportServer(
        worker_address="wA", host="127.0.0.1", port=0,
        shared_secret_provider=lambda peer: secret,
    )
    srv.start()
    try:
        payload = b"hidden-state" * 500
        tag = compute_payload_tag(
            job_id="0xabc", stage_index=0, sender_address="wA",
            payload=payload, shared_secret=secret,
        )
        srv.stash_local(job_id="0xabc", stage_index=0,
                        payload=payload, tag=tag)

        # Successful direct fetch
        got = fetch_direct(
            base_url=srv.public_url(),
            chunk_path_hint="/aicf/pipeline/0xabc/0",
            job_id="0xabc", stage_index=0,
        )
        assert got is not None
        fetched, sender, fetched_tag = got
        assert fetched == payload
        assert sender == "wA"
        assert verify_payload_tag(
            expected=fetched_tag, job_id="0xabc", stage_index=0,
            sender_address=sender, payload=fetched, shared_secret=secret,
        )
        # Tampered payload fails verification
        assert not verify_payload_tag(
            expected=fetched_tag, job_id="0xabc", stage_index=0,
            sender_address=sender, payload=payload + b"X",
            shared_secret=secret,
        )
        # Wrong secret fails verification
        assert not verify_payload_tag(
            expected=fetched_tag, job_id="0xabc", stage_index=0,
            sender_address=sender, payload=fetched, shared_secret=b"wrong",
        )
        # Unknown stage returns None
        assert fetch_direct(
            base_url=srv.public_url(),
            chunk_path_hint="/aicf/pipeline/0xdef/9",
            job_id="0xdef", stage_index=9,
        ) is None
    finally:
        srv.stop()


def test_direct_transport_fetch_returns_none_when_unreachable():
    import sys
    sys.path.insert(0, "ai/agent_runtime/src")
    from agent_runtime.pipeline_transport import fetch_direct
    # Port 1 is reserved and refuses TCP; the call must fail fast and
    # return None so the worker falls back to node-proxy.
    got = fetch_direct(
        base_url="http://127.0.0.1:1",
        chunk_path_hint="/aicf/pipeline/0xabc/0",
        job_id="0xabc", stage_index=0,
        timeout_sec=0.5,
    )
    assert got is None


# --------------------------------------------------------------------------- #
# Layer-range planning + locator                                              #
# --------------------------------------------------------------------------- #


def test_layer_range_planning_balances_chunks_and_covers_total():
    import sys
    sys.path.insert(0, "ai/flagship_agent/src")
    from flagship_agent.layer_range_inference import plan_layer_ranges
    # Standard 32-layer model across 4 stages = 8 each.
    ranges = plan_layer_ranges(32, 4)
    assert [r.start for r in ranges] == [0, 8, 16, 24]
    assert [r.end for r in ranges] == [8, 16, 24, 32]
    assert ranges[-1].end == 32  # last covers everything

    # Uneven: 7 layers across 3 stages -> last stage absorbs remainder
    ranges = plan_layer_ranges(7, 3)
    assert ranges[-1].end == 7
    total = sum(r.length for r in ranges)
    assert total == 7

    # More stages than layers — empty trailing ranges are OK.
    ranges = plan_layer_ranges(2, 5)
    assert len([r for r in ranges if r.length > 0]) == 2
    assert sum(r.length for r in ranges) == 2


def test_layer_locator_finds_layers_on_common_paths():
    import sys
    sys.path.insert(0, "ai/flagship_agent/src")
    from flagship_agent.layer_range_inference import locate_decoder_layers

    class _Bare:
        layers = [object(), object()]
    class _LlamaLike:
        class model:
            layers = [object()] * 3
    class _GPT2Like:
        class transformer:
            h = [object()] * 4

    assert len(locate_decoder_layers(_Bare())) == 2
    assert len(locate_decoder_layers(_LlamaLike())) == 3
    assert len(locate_decoder_layers(_GPT2Like())) == 4

    class _Mystery:
        pass
    with pytest.raises(ValueError):
        locate_decoder_layers(_Mystery())


@pytest.mark.skipif(
    True,
    reason=("LayerRangeRunner end-to-end needs torch + a tokenizer; "
            "covered in ai/flagship_agent/tests/ with a synthetic model"),
)
def test_layer_range_runner_end_to_end_synthetic():
    """Placeholder — covered separately in flagship_agent tests."""


if __name__ == "__main__":     # pragma: no cover — manual smoke
    pytest.main([__file__, "-v"])
