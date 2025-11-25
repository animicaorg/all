from __future__ import annotations

"""
Deploy & preflight models.

- DeployRequest/DeployResponse:
    Accepts a SIGNED CBOR transaction (hex) and optionally waits for a receipt
    after relaying it to the node RPC.

- PreflightRequest/PreflightResponse:
    Offline compile (no signing) of a Python-VM contract package to estimate
    deploy gas and compute a stable code hash before creating a real tx.

Notes
-----
These models intentionally avoid over-specifying receipt/block structures
returned by arbitrary nodes; we surface minimally-typed dictionaries when
needed and rely on adapters to normalize further.
"""

from typing import Any, Dict, List, Optional

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, PositiveInt, conint
except Exception:  # pragma: no cover - v1 fallback
    from pydantic.v1 import BaseModel, Field, PositiveInt  # type: ignore
    from pydantic.v1.types import conint  # type: ignore

from .common import Address, ChainId, Hash, Hex


# ---------------------------------------------------------------------------
# /deploy
# ---------------------------------------------------------------------------


class DeployRequest(BaseModel):
    """
    Relay a **signed** CBOR transaction to the node RPC.

    Fields
    ------
    chain_id: ChainId
        Target chain id; validated early to prevent mis-sends.
    from_address: Address
        Sender address for bookkeeping/UX (the tx is already signed; this is informational).
    raw_tx: Hex
        0x-prefixed hex of the signed CBOR transaction bytes.
    await_receipt: bool
        If True, block until the transaction is included or timeout expires.
    timeout_ms: Optional[int]
        Max time to wait for the receipt when `await_receipt=True`. Defaults to server policy.
    """

    chain_id: ChainId = Field(..., description="Target chain id.")
    from_address: Address = Field(..., alias="from", description="Sender address (informational).")
    raw_tx: Hex = Field(..., description="Signed CBOR transaction bytes (0x-hex).")
    await_receipt: bool = Field(
        default=True, description="If true, wait for inclusion and return receipt when available."
    )
    timeout_ms: Optional[conint(ge=1, le=10_0000)] = Field(  # up to ~100s
        default=None, description="Client-provided wait timeout (milliseconds)."
    )

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "forbid"
        anystr_strip_whitespace = True


class DeployResponse(BaseModel):
    """
    Response to a deploy relay.

    When `await_receipt=False`, only `tx_hash` is guaranteed. When true and the
    tx is included before timeout, `receipt` and optional derived fields are set.
    """

    tx_hash: Hash = Field(..., description="Transaction hash assigned by the node.")
    contract_address: Optional[Address] = Field(
        default=None, description="Address of deployed contract, if determinable from receipt."
    )
    receipt: Optional[Dict[str, Any]] = Field(
        default=None, description="Raw receipt object as returned by the node RPC."
    )
    block_hash: Optional[Hash] = Field(default=None, description="Block hash if included.")
    block_number: Optional[PositiveInt] = Field(default=None, description="Block number if included.")

    class Config:  # type: ignore[override]
        extra = "forbid"


# ---------------------------------------------------------------------------
# /preflight
# ---------------------------------------------------------------------------


class PreflightRequest(BaseModel):
    """
    Compile (offline) a Python-VM contract package and estimate deploy gas.

    This does **not** sign or broadcast anything. It is safe to call from untrusted
    frontends (subject to server-side resource limits).

    Fields
    ------
    chain_id: ChainId
        Target chain (used for deterministic code-hash domains and any feature flags).
    manifest: Dict[str, Any]
        Contract manifest JSON (ABI/functions/events/errors/etc.).
    source: str
        Contract source code (Python). For multi-file packages, send a concatenated source
        or use studio-services artifacts APIs beforehand and refer to them here.
    constructor_args: Optional[Dict[str, Any]]
        Arguments to the deploy/constructor entrypoint if applicable.
    """

    chain_id: ChainId = Field(..., description="Target chain id.")
    manifest: Dict[str, Any] = Field(..., description="Contract manifest JSON.")
    source: str = Field(..., description="Python source code of the contract.")
    constructor_args: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional constructor arguments."
    )

    class Config:  # type: ignore[override]
        extra = "forbid"
        anystr_strip_whitespace = True


class PreflightResponse(BaseModel):
    """
    Result of an offline compile + deploy-cost estimation.
    """

    code_hash: Hash = Field(..., description="Deterministic code hash computed from compiled artifact.")
    gas_estimate: PositiveInt = Field(..., description="Estimated intrinsic gas to deploy.")
    abi: Dict[str, Any] = Field(..., description="Normalized ABI as seen by clients.")
    diagnostics: List[str] = Field(default_factory=list, description="Compiler/analysis diagnostics (warnings/info).")

    class Config:  # type: ignore[override]
        extra = "forbid"
