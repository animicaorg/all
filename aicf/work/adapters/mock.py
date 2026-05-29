"""
aicf.work.adapters.mock
-----------------------

Deterministic, dependency-free mock implementations of the four work-layer
adapter protocols. Same behavior as the TS prototype's mocks so the
service tests can compare apples-to-apples.

These are also the *fallback* adapters: when ANIMICA_WORK_MODE=real but
the real implementation isn't shipped yet (or fails to import), the
resolver in ``aicf.work.adapters.__init__`` silently uses these so the
loop still completes — useful during a phased rollout.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any

from . import (
    ChainHead,
    PayoutInput,
    PayoutOutput,
    PlanInput,
    PlanOutput,
    PlannedTask,
    VerifyInput,
    VerifyOutput,
)


# ---------------------------------------------------------------------------
# Planner: rule-based job → task splitter.
# ---------------------------------------------------------------------------

_APP_BUILD_TASKS: list[PlannedTask] = [
    PlannedTask(
        title="Design schema and API contracts",
        description="Produce database schema and API contracts that match the prompt.",
        task_type="smart_contract",
        required_capabilities=["code_generation", "smart_contracts"],
        reward_weight=0.15,
        depends_on_indices=[],
    ),
    PlannedTask(
        title="Implement backend",
        description="Implement the backend per the schema/API contracts.",
        task_type="code_generation",
        required_capabilities=["code_generation", "typescript", "python"],
        reward_weight=0.30,
        depends_on_indices=[0],
    ),
    PlannedTask(
        title="Implement frontend",
        description="Build the frontend UI against the implemented backend.",
        task_type="code_generation",
        required_capabilities=["code_generation", "typescript", "javascript"],
        reward_weight=0.25,
        depends_on_indices=[1],
    ),
    PlannedTask(
        title="Generate tests",
        description="Cover the backend + frontend with unit and integration tests.",
        task_type="test_generation",
        required_capabilities=["code_generation", "test_runner"],
        reward_weight=0.15,
        depends_on_indices=[1, 2],
    ),
    PlannedTask(
        title="Prepare deployment",
        description="Produce deployment scripts, env, and runbook.",
        task_type="documentation",
        required_capabilities=["code_generation"],
        reward_weight=0.15,
        depends_on_indices=[3],
    ),
]

_CODE_GENERATION_TASKS: list[PlannedTask] = [
    PlannedTask(
        title="Write implementation",
        task_type="code_generation",
        required_capabilities=["code_generation"],
        reward_weight=0.7,
        depends_on_indices=[],
    ),
    PlannedTask(
        title="Write tests",
        task_type="test_generation",
        required_capabilities=["code_generation", "test_runner"],
        reward_weight=0.3,
        depends_on_indices=[0],
    ),
]


def _single_task(job_type: str, caps: list[str]) -> list[PlannedTask]:
    return [
        PlannedTask(
            title=f"Complete: {job_type}",
            task_type=job_type,
            required_capabilities=list(caps),
            reward_weight=1.0,
            depends_on_indices=[],
        )
    ]


@dataclass
class _MockPlanner:
    name: str = "mock-planner"

    def plan_job(self, inp: PlanInput) -> PlanOutput:
        if inp.job_type == "app_build":
            tasks = list(_APP_BUILD_TASKS)
        elif inp.job_type == "code_generation":
            tasks = list(_CODE_GENERATION_TASKS)
        else:
            tasks = _single_task(inp.job_type, inp.required_capabilities)
        # Carry caller-required capabilities into every task (conservative).
        if inp.required_capabilities:
            tasks = [
                PlannedTask(
                    title=t.title,
                    description=t.description,
                    task_type=t.task_type,
                    required_capabilities=list(
                        dict.fromkeys([*t.required_capabilities, *inp.required_capabilities])
                    ),
                    reward_weight=t.reward_weight,
                    depends_on_indices=list(t.depends_on_indices),
                )
                for t in tasks
            ]
        return PlanOutput(
            tasks=tasks,
            acceptance_criteria=(
                "Output addresses every explicit requirement in the prompt; "
                "passes provided tests; matches stated interface."
            ),
            verification_instructions=(
                "Run worker-supplied tests if any; diff outputs against acceptance "
                "criteria; require non-empty resultHash; flag empty or whitespace-"
                "only outputs as needs_review."
            ),
        )


mock_planner = _MockPlanner()


# ---------------------------------------------------------------------------
# Verifier: deterministic, signal-based scoring.
# ---------------------------------------------------------------------------


@dataclass
class _MockVerifier:
    name: str = "mock-verifier"

    def verify(self, inp: VerifyInput) -> VerifyOutput:
        text = (inp.output_text or "").strip()
        has_output = bool(text) or inp.output_json is not None
        artifacts = inp.artifact_urls or []
        has_artifacts = bool(artifacts)
        hash_looks_real = bool(re.fullmatch(r"[0-9a-f]{32,128}", inp.result_hash, re.I))
        score = 0.3
        if has_output:
            score += 0.3
        if has_artifacts:
            score += 0.15
        if hash_looks_real:
            score += 0.1
        if len(text) > 200:
            score += 0.1
        if inp.job_type == "test_generation" and re.search(r"test|expect|assert", text, re.I):
            score += 0.05
        score = min(1.0, score)
        if score >= 0.7:
            verdict = "accepted"
            notes = "Output meets acceptance criteria."
        elif score >= 0.45:
            verdict = "needs_review"
            notes = "Borderline output; surfacing to human review."
        else:
            verdict = "rejected"
            notes = (
                "Output missing or below quality bar; check that outputText/outputJson "
                "is set and resultHash is a hex digest."
            )
        return VerifyOutput(verdict=verdict, score=score, notes=notes)


mock_verifier = _MockVerifier()


# ---------------------------------------------------------------------------
# Payout: deterministic fake tx hash so the explorer-link path is testable.
# ---------------------------------------------------------------------------


def _fake_tx_hash(payout_id: str) -> str:
    return "0x" + hashlib.sha256(f"mock-payout:{payout_id}".encode()).hexdigest()


@dataclass
class _MockPayout:
    name: str = "mock-payout"
    mode: str = "mock"

    def send_payout(self, inp: PayoutInput) -> PayoutOutput:
        tx = _fake_tx_hash(inp.payout_id)
        base = os.environ.get("ANIMICA_EXPLORER_TX_URL", "https://explorer.animica.org/tx/")
        return PayoutOutput(
            status="paid",
            tx_hash=tx,
            explorer_url=base + tx,
            aicf_claim_id="mock-claim-" + inp.payout_id,
        )


mock_payout = _MockPayout()


# ---------------------------------------------------------------------------
# RPC: frozen values so UI / health endpoints can light up.
# ---------------------------------------------------------------------------


@dataclass
class _MockRpc:
    name: str = "mock-rpc"
    mode: str = "mock"

    def get_chain_head(self) -> ChainHead:
        return ChainHead(block_number=0, chain_id=1)

    def get_balance_anm(self, address: str) -> str:
        return "0"


mock_rpc = _MockRpc()


__all__ = ["mock_planner", "mock_verifier", "mock_payout", "mock_rpc"]
