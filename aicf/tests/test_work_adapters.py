"""Unit tests for the mock adapters."""

from __future__ import annotations

from aicf.work.adapters import PlanInput, VerifyInput, PayoutInput
from aicf.work.adapters.mock import (
    mock_payout,
    mock_planner,
    mock_rpc,
    mock_verifier,
)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def test_app_build_splits_into_five_dependent_tasks():
    out = mock_planner.plan_job(
        PlanInput(prompt="Build a thing", job_type="app_build",
                  required_capabilities=[], total_reward_anm="1")
    )
    assert len(out.tasks) == 5
    total_weight = sum(t.reward_weight for t in out.tasks)
    assert abs(total_weight - 1.0) < 1e-6
    # Frontend depends on backend; backend depends on schema.
    assert out.tasks[1].depends_on_indices == [0]
    assert out.tasks[2].depends_on_indices == [1]


def test_code_generation_splits_into_impl_and_tests():
    out = mock_planner.plan_job(
        PlanInput(prompt="Write a function", job_type="code_generation",
                  required_capabilities=[], total_reward_anm="1")
    )
    assert len(out.tasks) == 2
    assert abs(sum(t.reward_weight for t in out.tasks) - 1.0) < 1e-6


def test_unknown_job_types_get_single_task():
    out = mock_planner.plan_job(
        PlanInput(prompt="x", job_type="research",
                  required_capabilities=["llm_inference"], total_reward_anm="1")
    )
    assert len(out.tasks) == 1
    assert "llm_inference" in out.tasks[0].required_capabilities


def test_caller_required_caps_propagate_to_every_task():
    out = mock_planner.plan_job(
        PlanInput(prompt="x", job_type="app_build",
                  required_capabilities=["typescript", "gpu"], total_reward_anm="1")
    )
    for t in out.tasks:
        assert "typescript" in t.required_capabilities
        assert "gpu" in t.required_capabilities


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def test_verifier_rejects_empty_output():
    out = mock_verifier.verify(
        VerifyInput(job_type="code_generation", prompt="x", result_hash="abc",
                    artifact_urls=[])
    )
    assert out.verdict == "rejected"
    assert out.score < 0.45


def test_verifier_accepts_meaty_output_with_artifacts_and_real_hash():
    out = mock_verifier.verify(
        VerifyInput(
            job_type="code_generation",
            prompt="x",
            output_text="x" * 300,
            artifact_urls=["https://example.com/a"],
            result_hash="a" * 64,
        )
    )
    assert out.verdict == "accepted"
    assert out.score >= 0.7


def test_verifier_rewards_test_like_output_for_test_generation():
    a = mock_verifier.verify(
        VerifyInput(job_type="test_generation", prompt="x",
                    output_text="this is just prose", result_hash="a" * 64,
                    artifact_urls=[])
    )
    b = mock_verifier.verify(
        VerifyInput(job_type="test_generation", prompt="x",
                    output_text="describe.test it('x', () => { expect(1).toBe(1) })",
                    result_hash="a" * 64, artifact_urls=[])
    )
    assert b.score > a.score


# ---------------------------------------------------------------------------
# Payout
# ---------------------------------------------------------------------------


def test_payout_produces_deterministic_tx_hash():
    a = mock_payout.send_payout(PayoutInput("p1", "anim1abc", "1", "r1"))
    b = mock_payout.send_payout(PayoutInput("p1", "anim1abc", "1", "r1"))
    assert a.tx_hash == b.tx_hash
    assert a.status == "paid"
    assert a.tx_hash and a.tx_hash.startswith("0x") and len(a.tx_hash) == 66


def test_payout_different_ids_different_hashes():
    a = mock_payout.send_payout(PayoutInput("p1", "anim1abc", "1", "r1"))
    b = mock_payout.send_payout(PayoutInput("p2", "anim1abc", "1", "r2"))
    assert a.tx_hash != b.tx_hash


# ---------------------------------------------------------------------------
# RPC
# ---------------------------------------------------------------------------


def test_rpc_returns_frozen_values_without_network():
    head = mock_rpc.get_chain_head()
    assert head.chain_id == 1
    assert mock_rpc.get_balance_anm("anim1xxx") == "0"
