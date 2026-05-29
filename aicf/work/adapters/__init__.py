"""
aicf.work.adapters
------------------

Adapter contracts for the four pieces of the work loop that have a
swappable "mock vs real" backend:

  - PlannerAdapter   — split a prompt into tasks
  - VerifierAdapter  — score a submitted result
  - PayoutAdapter    — settle ANM rewards on chain
  - RpcAdapter       — talk to the Animica node

Selection happens via env vars (mirrors the TS prototype):

    ANIMICA_WORK_MODE=mock           # default; all-mocks bundle
    ANIMICA_WORK_MODE=real           # try real, fall back to mock per-adapter
    ANIMICA_WORK_PLANNER=mock|real   # per-adapter overrides
    ANIMICA_WORK_VERIFIER=mock|real
    ANIMICA_WORK_PAYOUT=mock|real
    ANIMICA_WORK_RPC=mock|real

Tests bypass env and pass an explicit ``AdapterBundle`` directly.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedTask:
    title: str
    task_type: str
    required_capabilities: list[str]
    reward_weight: float       # 0..1; the service normalizes
    depends_on_indices: list[int]
    description: str | None = None


@dataclass(frozen=True)
class PlanInput:
    prompt: str
    job_type: str
    required_capabilities: list[str]
    total_reward_anm: str


@dataclass(frozen=True)
class PlanOutput:
    tasks: list[PlannedTask]
    acceptance_criteria: str
    verification_instructions: str


@dataclass(frozen=True)
class VerifyInput:
    job_type: str
    prompt: str
    result_hash: str
    output_text: str | None = None
    output_json: Any | None = None
    artifact_urls: list[str] | None = None


@dataclass(frozen=True)
class VerifyOutput:
    verdict: str           # "accepted" | "rejected" | "needs_review"
    score: float           # 0..1
    notes: str | None = None
    test_output: Any | None = None


@dataclass(frozen=True)
class PayoutInput:
    payout_id: str
    worker_wallet: str
    amount_anm: str
    result_id: str
    memo: str | None = None


@dataclass(frozen=True)
class PayoutOutput:
    status: str            # "submitted" | "paid" | "failed" | "manual_review"
    tx_hash: str | None = None
    explorer_url: str | None = None
    aicf_claim_id: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class ChainHead:
    block_number: int
    chain_id: int


# ---------------------------------------------------------------------------
# Protocols — services depend on these, not on concrete implementations.
# ---------------------------------------------------------------------------


@runtime_checkable
class PlannerAdapter(Protocol):
    name: str

    def plan_job(self, inp: PlanInput) -> PlanOutput: ...


@runtime_checkable
class VerifierAdapter(Protocol):
    name: str

    def verify(self, inp: VerifyInput) -> VerifyOutput: ...


@runtime_checkable
class PayoutAdapter(Protocol):
    name: str
    mode: str  # "mock" | "real"

    def send_payout(self, inp: PayoutInput) -> PayoutOutput: ...


@runtime_checkable
class RpcAdapter(Protocol):
    name: str
    mode: str

    def get_chain_head(self) -> ChainHead: ...
    def get_balance_anm(self, address: str) -> str: ...


@dataclass(frozen=True)
class AdapterBundle:
    planner: PlannerAdapter
    verifier: VerifierAdapter
    payout: PayoutAdapter
    rpc: RpcAdapter


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

_bundle: AdapterBundle | None = None


def _pick(env_key: str) -> str:
    default = os.environ.get("ANIMICA_WORK_MODE", "mock").lower()
    return os.environ.get(env_key, default).lower()


def _try_import(modname: str, attr: str, fallback: Any) -> Any:
    """Load a real adapter or fall back to the mock if the module isn't shipped yet."""
    try:
        mod = importlib.import_module(modname)
    except ImportError:
        return fallback
    return getattr(mod, attr, fallback)


def get_adapters() -> AdapterBundle:
    """Lazy-construct the adapter bundle from env. Cached process-wide."""
    global _bundle
    if _bundle is not None:
        return _bundle
    from . import mock

    planner = (
        _try_import("aicf.work.adapters.real_planner", "real_planner", mock.mock_planner)
        if _pick("ANIMICA_WORK_PLANNER") == "real"
        else mock.mock_planner
    )
    verifier = (
        _try_import("aicf.work.adapters.real_verifier", "real_verifier", mock.mock_verifier)
        if _pick("ANIMICA_WORK_VERIFIER") == "real"
        else mock.mock_verifier
    )
    payout = (
        _try_import("aicf.work.adapters.real_payout", "real_payout", mock.mock_payout)
        if _pick("ANIMICA_WORK_PAYOUT") == "real"
        else mock.mock_payout
    )
    rpc = (
        _try_import("aicf.work.adapters.real_rpc", "real_rpc", mock.mock_rpc)
        if _pick("ANIMICA_WORK_RPC") == "real"
        else mock.mock_rpc
    )

    _bundle = AdapterBundle(planner=planner, verifier=verifier, payout=payout, rpc=rpc)
    return _bundle


def set_adapters_for_test(bundle: AdapterBundle | None) -> None:
    """Swap the cached bundle. Tests use this; production code does not."""
    global _bundle
    _bundle = bundle


__all__ = [
    "PlannedTask", "PlanInput", "PlanOutput",
    "VerifyInput", "VerifyOutput",
    "PayoutInput", "PayoutOutput",
    "ChainHead",
    "PlannerAdapter", "VerifierAdapter", "PayoutAdapter", "RpcAdapter",
    "AdapterBundle",
    "get_adapters", "set_adapters_for_test",
]
