from __future__ import annotations

"""
Deploy and preflight models.

These models are intentionally backward-compatible with older request/response
shapes used by studio-web and tests.
"""

from typing import Any, Dict, List, Optional

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, PositiveInt, model_validator
except Exception:  # pragma: no cover - v1 fallback
    from pydantic.v1 import BaseModel, Field, PositiveInt, root_validator  # type: ignore

from .common import Address, ChainId, Hash


class DeployRequest(BaseModel):
    """
    Relay a signed transaction payload to the node RPC.

    Accepted tx field aliases (input):
    - raw_tx (canonical)
    - rawTx
    - tx
    - signed_tx_hex
    - tx_cbor

    Accepted wait aliases:
    - await_receipt (canonical)
    - wait_for_receipt
    - wait
    - awaitReceipt
    """

    chain_id: Optional[ChainId] = Field(default=None, description="Target chain id.")
    from_address: Optional[Address] = Field(
        default=None,
        alias="from",
        description="Sender address (informational).",
    )
    raw_tx: str = Field(..., description="Signed transaction bytes encoded as hex.")
    await_receipt: bool = Field(
        default=False,
        description="If true, wait for inclusion and return receipt when available.",
    )
    timeout_ms: int = Field(
        default=60_000,
        ge=1,
        le=600_000,
        description="Max wait time for receipt in milliseconds.",
    )
    poll_interval_ms: int = Field(
        default=1_000,
        ge=10,
        le=60_000,
        description="Polling interval when waiting for receipt.",
    )

    @staticmethod
    def _normalize_hex(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("raw_tx must be a hex string")
        v = value.strip()
        if not v:
            raise ValueError("raw_tx is required")
        if v.startswith("0x") or v.startswith("0X"):
            v = v[2:]
        if not v:
            raise ValueError("raw_tx is required")
        if len(v) % 2 != 0:
            raise ValueError("raw_tx must have even hex length")
        try:
            int(v, 16)
        except Exception as e:
            raise ValueError("raw_tx must be valid hex") from e
        return "0x" + v.lower()

    if "model_validator" in globals():

        @model_validator(mode="before")
        @classmethod
        def _coerce_aliases(cls, data: Any) -> Any:  # type: ignore[override]
            if not isinstance(data, dict):
                return data
            out = dict(data)

            if "raw_tx" not in out:
                for k in ("rawTx", "tx", "signed_tx_hex", "tx_cbor"):
                    if out.get(k) is not None:
                        out["raw_tx"] = out[k]
                        break

            if "await_receipt" not in out:
                for k in ("wait_for_receipt", "wait", "awaitReceipt"):
                    if k in out:
                        out["await_receipt"] = out[k]
                        break

            if "chain_id" not in out and "chainId" in out:
                out["chain_id"] = out["chainId"]

            if "timeout_ms" not in out:
                for k in ("timeoutMs",):
                    if k in out:
                        out["timeout_ms"] = out[k]
                        break

            if "poll_interval_ms" not in out:
                for k in ("pollIntervalMs",):
                    if k in out:
                        out["poll_interval_ms"] = out[k]
                        break

            if "from_address" not in out and "from" in out:
                out["from_address"] = out["from"]

            return out

        @model_validator(mode="after")
        def _normalize(self) -> "DeployRequest":  # type: ignore[override]
            self.raw_tx = self._normalize_hex(self.raw_tx)
            return self

    else:  # pragma: no cover - pydantic v1 fallback

        @root_validator(pre=True)
        def _coerce_aliases_v1(cls, values: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
            out = dict(values or {})
            if "raw_tx" not in out:
                for k in ("rawTx", "tx", "signed_tx_hex", "tx_cbor"):
                    if out.get(k) is not None:
                        out["raw_tx"] = out[k]
                        break
            if "await_receipt" not in out:
                for k in ("wait_for_receipt", "wait", "awaitReceipt"):
                    if k in out:
                        out["await_receipt"] = out[k]
                        break
            if "chain_id" not in out and "chainId" in out:
                out["chain_id"] = out["chainId"]
            if "timeout_ms" not in out and "timeoutMs" in out:
                out["timeout_ms"] = out["timeoutMs"]
            if "poll_interval_ms" not in out and "pollIntervalMs" in out:
                out["poll_interval_ms"] = out["pollIntervalMs"]
            if "from_address" not in out and "from" in out:
                out["from_address"] = out["from"]
            if "raw_tx" in out:
                out["raw_tx"] = cls._normalize_hex(out["raw_tx"])
            return out

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "ignore"


class DeployResponse(BaseModel):
    tx_hash: Hash = Field(
        ...,
        alias="txHash",
        description="Transaction hash assigned by the node.",
    )
    contract_address: Optional[Address] = Field(
        default=None,
        alias="contractAddress",
        description="Address of deployed contract, if known.",
    )
    receipt: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Raw receipt object as returned by node RPC.",
    )
    block_hash: Optional[Hash] = Field(
        default=None,
        alias="blockHash",
        description="Block hash if included.",
    )
    block_number: Optional[PositiveInt] = Field(
        default=None,
        alias="blockNumber",
        description="Block number if included.",
    )

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "forbid"


class PreflightRequest(BaseModel):
    chain_id: ChainId = Field(default=1, description="Target chain id.")
    manifest: Dict[str, Any] = Field(default_factory=dict, description="Contract manifest JSON.")
    source: str = Field(default="", description="Python source code.")
    code_bytes: Optional[str] = Field(
        default=None,
        description="Optional compiled bytes as 0x-hex.",
    )
    constructor_args: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional constructor arguments.",
    )
    simulate: bool = Field(default=False, description="Run local dry-run simulation.")

    if "model_validator" in globals():

        @model_validator(mode="before")
        @classmethod
        def _coerce_preflight_aliases(cls, data: Any) -> Any:  # type: ignore[override]
            if not isinstance(data, dict):
                return data
            out = dict(data)
            if "chain_id" not in out and "chainId" in out:
                out["chain_id"] = out["chainId"]
            if "constructor_args" not in out and "constructorArgs" in out:
                out["constructor_args"] = out["constructorArgs"]
            if "code_bytes" not in out:
                for k in ("codeBytes", "code", "rawTx", "raw_tx"):
                    if out.get(k) is not None:
                        out["code_bytes"] = out[k]
                        break
            if "simulate" not in out:
                out["simulate"] = bool(out.get("estimateGas") or out.get("simulate"))
            return out

    else:  # pragma: no cover

        @root_validator(pre=True)
        def _coerce_preflight_aliases_v1(cls, values: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
            out = dict(values or {})
            if "chain_id" not in out and "chainId" in out:
                out["chain_id"] = out["chainId"]
            if "constructor_args" not in out and "constructorArgs" in out:
                out["constructor_args"] = out["constructorArgs"]
            if "code_bytes" not in out:
                for k in ("codeBytes", "code", "rawTx", "raw_tx"):
                    if out.get(k) is not None:
                        out["code_bytes"] = out[k]
                        break
            if "simulate" not in out:
                out["simulate"] = bool(out.get("estimateGas") or out.get("simulate"))
            return out

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "ignore"


class PreflightResponse(BaseModel):
    code_hash: Optional[Hash] = Field(default=None, alias="codeHash")
    gas_estimate: Optional[PositiveInt] = Field(default=None, alias="gasEstimate")
    abi: Dict[str, Any] = Field(default_factory=dict)
    diagnostics: List[str] = Field(default_factory=list)
    ok: bool = Field(default=True)
    error: Optional[str] = Field(default=None)

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "ignore"
