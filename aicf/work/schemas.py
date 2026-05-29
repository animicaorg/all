"""
aicf.work.schemas
-----------------

Pydantic v2 request/response models for the Animica useful-work AI layer.

Enum tuples mirror ``apps/chat-animica/src/shared/work-schemas.ts`` byte-for-
byte so the TS Studio UI and the Python AICF layer never disagree on
allowed status names, job types, modes, or capabilities. When you change
one side, change the other.

Decimal strings (``reward_amount_anm``, etc.) are validated by regex —
not coerced through float — because ANM amounts span the full 18 + 9
range used elsewhere in the chain (see ``aicf.work.decimals``).
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# ---------------------------------------------------------------------------
# Enum tuples — single source of truth.
# ---------------------------------------------------------------------------

JOB_TYPES: tuple[str, ...] = (
    "code_generation",
    "code_fix",
    "code_review",
    "test_generation",
    "documentation",
    "smart_contract",
    "app_build",
    "llm_inference",
    "embedding",
    "summarization",
    "classification",
    "data_processing",
    "security_review",
    "simulation",
    "research",
    "creative_asset",
    "custom",
)
JobType = Literal[
    "code_generation",
    "code_fix",
    "code_review",
    "test_generation",
    "documentation",
    "smart_contract",
    "app_build",
    "llm_inference",
    "embedding",
    "summarization",
    "classification",
    "data_processing",
    "security_review",
    "simulation",
    "research",
    "creative_asset",
    "custom",
]

JOB_STATUSES: tuple[str, ...] = (
    "draft",
    "submitted",
    "planning",
    "open",
    "claimed",
    "running",
    "result_submitted",
    "verifying",
    "accepted",
    "rejected",
    "paid",
    "failed",
    "cancelled",
    "expired",
    "manual_review",
)

TASK_STATUSES: tuple[str, ...] = (
    "open",
    "claimed",
    "running",
    "submitted",
    "accepted",
    "rejected",
    "paid",
    "failed",
)

VERIFICATION_MODES: tuple[str, ...] = (
    "user_acceptance",
    "single_verifier",
    "multi_verifier",
    "automated_tests",
    "deterministic_check",
    "admin_review",
    "hybrid",
)
VerificationMode = Literal[
    "user_acceptance",
    "single_verifier",
    "multi_verifier",
    "automated_tests",
    "deterministic_check",
    "admin_review",
    "hybrid",
]

VERIFICATION_VERDICTS: tuple[str, ...] = (
    "accepted",
    "rejected",
    "needs_review",
)
VerificationVerdict = Literal["accepted", "rejected", "needs_review"]

RESULT_VISIBILITIES: tuple[str, ...] = (
    "private",
    "public_result_only",
    "public_full",
)
ResultVisibility = Literal["private", "public_result_only", "public_full"]

DEVICE_TYPES: tuple[str, ...] = (
    "cpu",
    "cuda",
    "metal",
    "rocm",
    "opencl",
    "remote_api",
    "unknown",
)
DeviceType = Literal["cpu", "cuda", "metal", "rocm", "opencl", "remote_api", "unknown"]

WORKER_STATUSES: tuple[str, ...] = ("idle", "working", "offline", "banned")

CAPABILITIES: tuple[str, ...] = (
    "llm_inference",
    "code_generation",
    "code_execution",
    "python",
    "javascript",
    "typescript",
    "rust",
    "smart_contracts",
    "embeddings",
    "image_generation",
    "test_runner",
    "static_analysis",
    "security_tools",
    "gpu",
    "high_memory",
    "rag",
    "internet_disabled",
    "internet_enabled",
)
Capability = Literal[
    "llm_inference",
    "code_generation",
    "code_execution",
    "python",
    "javascript",
    "typescript",
    "rust",
    "smart_contracts",
    "embeddings",
    "image_generation",
    "test_runner",
    "static_analysis",
    "security_tools",
    "gpu",
    "high_memory",
    "rag",
    "internet_disabled",
    "internet_enabled",
]

PAYOUT_STATUSES: tuple[str, ...] = (
    "pending",
    "approved",
    "submitted",
    "paid",
    "failed",
    "withheld",
    "manual_review",
)

CLAIM_STATUSES: tuple[str, ...] = (
    "active",
    "released",
    "expired",
    "completed",
)

# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

_ANM_RE = re.compile(r"^\d{1,36}(\.\d{1,18})?$")
_BECH32_RE = re.compile(r"^anim1[0-9a-z]{20,90}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{32,128}$")

AnmAmount = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64),
]
BechAddress = Annotated[
    str,
    StringConstraints(min_length=20, max_length=120),
]
HexDigest = Annotated[
    str,
    StringConstraints(min_length=32, max_length=128),
]


def _validate_anm(v: str) -> str:
    if not _ANM_RE.match(v):
        raise ValueError("must be a positive decimal string (ANM)")
    # Cheap > 0 check without converting to float.
    if all(ch in "0." for ch in v):
        raise ValueError("must be > 0")
    return v


def _validate_bech(v: str) -> str:
    if not _BECH32_RE.match(v):
        raise ValueError("must be a bech32 anim1... address")
    return v


def _validate_hex(v: str) -> str:
    if not _HEX_RE.match(v):
        raise ValueError("must be a hex digest (32-128 chars)")
    return v.lower()


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    # protected_namespaces=() silences the "model_info conflicts with model_*
    # protected namespace" warning Pydantic v2 raises by default; SubmitResultRequest
    # legitimately needs a model_info field name that mirrors the TS schema.
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=False,
        protected_namespaces=(),
    )


class CreateJobRequest(_Strict):
    title: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    description: Annotated[str, StringConstraints(max_length=8000)] | None = None
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    job_type: JobType
    reward_amount_anm: AnmAmount
    creator_wallet: BechAddress | None = None
    creator_user_id: str | None = None
    required_capabilities: list[Capability] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=100)
    max_workers: int = Field(default=1, ge=1, le=50)
    verification_mode: VerificationMode = "user_acceptance"
    result_visibility: ResultVisibility = "private"
    plan: bool = True
    expires_in_seconds: int | None = Field(default=None, ge=60, le=7 * 24 * 3600)
    idempotency_key: Annotated[str, StringConstraints(min_length=8, max_length=120)] | None = None

    @field_validator("reward_amount_anm")
    @classmethod
    def _validate_reward(cls, v: str) -> str:
        return _validate_anm(v)

    @field_validator("creator_wallet")
    @classmethod
    def _validate_wallet(cls, v: str | None) -> str | None:
        return _validate_bech(v) if v is not None else None


class RegisterWorkerRequest(_Strict):
    wallet_address: BechAddress
    machine_id: Annotated[str, StringConstraints(min_length=8, max_length=128)]
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    device_type: DeviceType = "cpu"
    hardware_summary: dict[str, Any] | None = None
    capabilities: list[Capability] = Field(default_factory=list)

    @field_validator("wallet_address")
    @classmethod
    def _validate_wallet(cls, v: str) -> str:
        return _validate_bech(v)


class HeartbeatWorkerRequest(_Strict):
    worker_id: Annotated[str, StringConstraints(min_length=1)]


class ClaimNextRequest(_Strict):
    worker_id: Annotated[str, StringConstraints(min_length=1)]
    offered_capabilities: list[Capability] | None = None
    lease_seconds: int = Field(default=300, ge=60, le=1800)
    request_id: Annotated[str, StringConstraints(min_length=8, max_length=120)] | None = None


class SubmitResultRequest(_Strict):
    job_id: Annotated[str, StringConstraints(min_length=1)]
    task_id: Annotated[str, StringConstraints(min_length=1)] | None = None
    worker_id: Annotated[str, StringConstraints(min_length=1)]
    claim_id: Annotated[str, StringConstraints(min_length=1)]
    output_text: Annotated[str, StringConstraints(max_length=200_000)] | None = None
    output_json: Any | None = None
    artifact_urls: list[str] = Field(default_factory=list)
    result_hash: HexDigest
    logs_hash: HexDigest | None = None
    model_info: dict[str, Any] | None = None
    runtime_info: dict[str, Any] | None = None

    @field_validator("result_hash")
    @classmethod
    def _validate_result_hash(cls, v: str) -> str:
        return _validate_hex(v)

    @field_validator("logs_hash")
    @classmethod
    def _validate_logs_hash(cls, v: str | None) -> str | None:
        return _validate_hex(v) if v is not None else None


class VerifyResultRequest(_Strict):
    verdict: VerificationVerdict | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: Annotated[str, StringConstraints(max_length=8000)] | None = None
    test_output: Any | None = None
    verifier_worker_id: Annotated[str, StringConstraints(min_length=1)] | None = None
    mode: VerificationMode | None = None


class ApprovePayoutRequest(_Strict):
    amount_override_anm: AnmAmount | None = None
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None

    @field_validator("amount_override_anm")
    @classmethod
    def _validate_override(cls, v: str | None) -> str | None:
        return _validate_anm(v) if v is not None else None


# Re-exports so the dispatcher can introspect the full enum surface without
# crawling the module.
__all__ = [
    "JOB_TYPES", "JobType",
    "JOB_STATUSES", "TASK_STATUSES",
    "VERIFICATION_MODES", "VerificationMode",
    "VERIFICATION_VERDICTS", "VerificationVerdict",
    "RESULT_VISIBILITIES", "ResultVisibility",
    "DEVICE_TYPES", "DeviceType",
    "WORKER_STATUSES",
    "CAPABILITIES", "Capability",
    "PAYOUT_STATUSES", "CLAIM_STATUSES",
    "CreateJobRequest", "RegisterWorkerRequest", "HeartbeatWorkerRequest",
    "ClaimNextRequest", "SubmitResultRequest", "VerifyResultRequest",
    "ApprovePayoutRequest",
]
