"""
End-to-end smoke test for the useful-work bridge:
mining.ai_worker.run -> mining.uw_inbox -> compute.receipt.v1 envelope ->
core.usefulwork.verify_proof -> bonus AICF credits.

These tests exercise the same path that runs inside the live miner:
each completed AI job produces a CBOR-encoded UsefulWorkProof envelope
that the on-chain UWP verifier accepts.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from core.usefulwork import (
    ShareContext,
    VerifyStatus,
    decode_proof_from_hex,
    verify_proof,
)
from mining import uw_inbox
from mining.ai_worker import run as ai_worker_run


@pytest.mark.asyncio
async def test_ai_worker_produces_attachable_proof():
    os.environ.setdefault("ANIMICA_AI_WORKER_INTERVAL_S", "0.3")
    os.environ.setdefault("AI_SIM_LAT_MS", "200")
    uw_inbox.reset()
    stop = asyncio.Event()

    async def _stop_after(d: float) -> None:
        await asyncio.sleep(d)
        stop.set()

    stopper = asyncio.create_task(_stop_after(2.0))
    await ai_worker_run(stop)
    await stopper

    pending = uw_inbox.drain(max_n=8)
    assert pending, "ai_worker run did not emit any UWP envelopes"

    hex_envelope = pending[0]
    proof = decode_proof_from_hex(hex_envelope)
    assert proof.scheme_id == "compute.receipt.v1"
    assert len(proof.output_commitment) == 32

    ctx = ShareContext(
        job_id="test-job",
        nonce=b"\x00" * 8,
        mix_seed=b"\x00" * 32,
        height=1,
        miner_address="anim1miner-test",
        timestamp=int(time.time()),
    )
    result = verify_proof(proof, ctx)
    assert result.status == VerifyStatus.ACCEPTED, (
        f"verifier rejected proof: {result.reason}"
    )
    assert result.bonus_credits > 0, "expected non-zero bonus credits"
