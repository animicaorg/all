from __future__ import annotations

"""
Verification models

- VerifyRequest: submit source + manifest to recompile and match a contract's
  code hash against an on-chain subject (either a contract address or a deploy tx).
- VerifyStatus: lightweight job/status view for polling.
- VerifyResult: final, detailed outcome with normalized ABI and diagnostics.

Notes
-----
* Exactly one of `address` or `tx_hash` must be provided.
* `expected_code_hash` (alias: `code_hash`) lets clients assert what they think
  the code hash should be; the service will still compute `computed_code_hash`
  and indicate `matched` explicitly.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, model_validator
    _IS_PYDANTIC_V2 = True
except Exception:  # pragma: no cover - v1 fallback
    from pydantic.v1 import BaseModel, Field, root_validator  # type: ignore
    _IS_PYDANTIC_V2 = False

from .common import Address, ChainId, Hash


class VerifyState(str, Enum):
    pending = "pending"
    running = "running"
    matched = "matched"
    mismatch = "mismatch"
    error = "error"


class VerifyRequest(BaseModel):
    """
    Request to verify contract source/manifest against an on-chain subject.

    Fields
    ------
    chain_id: ChainId
        Target chain id.
    address: Optional[Address]
        Contract address to verify (mutually exclusive with `tx_hash`).
    tx_hash: Optional[Hash]
        Deploy transaction hash to verify (mutually exclusive with `address`).
    source: str
        Contract source code (Python).
    manifest: Dict[str, Any]
        Contract manifest JSON (ABI, metadata).
    expected_code_hash: Optional[Hash] (alias: code_hash)
        Optional expected code hash for assertion; does not replace the computed hash.
    """

    chain_id: ChainId = Field(..., description="Target chain id.")
    address: Optional[Address] = Field(
        default=None, description="Contract address to verify (exclusive with tx_hash)."
    )
    tx_hash: Optional[Hash] = Field(
        default=None, description="Deploy transaction hash to verify (exclusive with address)."
    )
    source: str = Field(..., description="Python contract source.")
    manifest: Dict[str, Any] = Field(..., description="Contract manifest JSON.")
    expected_code_hash: Optional[Hash] = Field(
        default=None, alias="code_hash", description="Optional asserted code hash."
    )

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "forbid"
        anystr_strip_whitespace = True

    if _IS_PYDANTIC_V2:
        @model_validator(mode="after")
        def _one_of_subject(cls, values: "VerifyRequest") -> "VerifyRequest":  # type: ignore[override]
            addr, txh = values.address, values.tx_hash
            if bool(addr) == bool(txh):
                raise ValueError("Provide exactly one of `address` or `tx_hash`.")
            return values
    else:  # pragma: no cover - v1 compatibility
        @root_validator
        def _one_of_subject_v1(cls, values: Dict[str, Any]) -> Dict[str, Any]:
            addr, txh = values.get("address"), values.get("tx_hash")
            if bool(addr) == bool(txh):
                raise ValueError("Provide exactly one of `address` or `tx_hash`.")
            return values


class VerifyStatus(BaseModel):
    """
    Lightweight polling view for a verification job.
    """

    job_id: str = Field(..., description="Deterministic job id.")
    status: VerifyState = Field(..., description="Current job state.")
    chain_id: ChainId = Field(..., description="Target chain id.")
    address: Optional[Address] = Field(default=None, description="Subject address, if applicable.")
    tx_hash: Optional[Hash] = Field(default=None, description="Subject deploy tx, if applicable.")
    computed_code_hash: Optional[Hash] = Field(
        default=None, description="Code hash computed by the service, if available."
    )
    error: Optional[str] = Field(default=None, description="Error message if state=error.")
    created_at: Optional[str] = Field(default=None, description="ISO-8601 created timestamp.")
    updated_at: Optional[str] = Field(default=None, description="ISO-8601 updated timestamp.")

    class Config:  # type: ignore[override]
        extra = "forbid"


class VerifyResult(BaseModel):
    """
    Final, detailed verification outcome.
    """

    job_id: str = Field(..., description="Deterministic job id.")
    status: VerifyState = Field(..., description="Final status (matched/mismatch/error).")
    matched: bool = Field(..., description="True if computed_code_hash == expected_code_hash (when provided) or on-chain.")
    chain_id: ChainId = Field(..., description="Target chain id.")
    address: Optional[Address] = Field(default=None, description="Subject address, if applicable.")
    tx_hash: Optional[Hash] = Field(default=None, description="Subject deploy tx, if applicable.")
    computed_code_hash: Hash = Field(..., description="Code hash computed from recompiled artifact.")
    expected_code_hash: Optional[Hash] = Field(
        default=None, description="Expected or asserted code hash, if provided by the client."
    )
    abi: Dict[str, Any] = Field(..., description="Normalized ABI used for clients/tools.")
    diagnostics: List[str] = Field(default_factory=list, description="Compiler/normalization diagnostics.")
    error: Optional[str] = Field(default=None, description="Error message if status=error.")

    class Config:  # type: ignore[override]
        extra = "forbid"


__all__ = ["VerifyRequest", "VerifyStatus", "VerifyResult", "VerifyState"]
