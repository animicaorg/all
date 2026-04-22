from __future__ import annotations

"""Faucet request/response models (backward-compatible aliases)."""

from typing import Any, Dict, Optional

try:
    from pydantic import BaseModel, Field, PositiveInt, conint, model_validator
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field, PositiveInt, root_validator  # type: ignore
    from pydantic.v1.types import conint  # type: ignore

from .common import Address, ChainId, Hash

_DEFAULT_ADDR = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqey8k2v"


class FaucetRequest(BaseModel):
    chain_id: Optional[ChainId] = Field(default=None, description="Target chain id.")
    address: Address = Field(
        default=_DEFAULT_ADDR,
        description="Recipient address.",
    )
    amount: Optional[conint(ge=1)] = Field(  # type: ignore[misc]
        default=None,
        description="Requested amount in base units (optional).",
    )

    if "model_validator" in globals():

        @model_validator(mode="before")
        @classmethod
        def _coerce_aliases(cls, data: Any) -> Any:  # type: ignore[override]
            if not isinstance(data, dict):
                return data
            out = dict(data)
            if "address" not in out:
                for k in ("to", "recipient"):
                    if out.get(k):
                        out["address"] = out[k]
                        break
            if "chain_id" not in out and "chainId" in out:
                out["chain_id"] = out["chainId"]
            if "amount" in out and isinstance(out["amount"], str):
                s = out["amount"].strip()
                if s:
                    out["amount"] = int(s)
            return out

    else:  # pragma: no cover

        @root_validator(pre=True)
        def _coerce_aliases_v1(cls, values: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
            out = dict(values or {})
            if "address" not in out:
                for k in ("to", "recipient"):
                    if out.get(k):
                        out["address"] = out[k]
                        break
            if "chain_id" not in out and "chainId" in out:
                out["chain_id"] = out["chainId"]
            if "amount" in out and isinstance(out["amount"], str):
                s = out["amount"].strip()
                if s:
                    out["amount"] = int(s)
            return out

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "ignore"


class FaucetResponse(BaseModel):
    tx_hash: Optional[Hash] = Field(
        default=None,
        alias="txHash",
        description="Transaction hash of faucet transfer (if broadcast).",
    )
    granted: PositiveInt = Field(
        ...,
        alias="amount",
        description="Amount granted in base units.",
    )
    address: Optional[Address] = Field(default=None)
    new_balance: Optional[conint(ge=0)] = Field(default=None)  # type: ignore[misc]
    receipt: Optional[Dict[str, Any]] = Field(default=None)
    message: Optional[str] = Field(default=None)
    limits: Optional[Dict[str, Any]] = Field(default=None)

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "ignore"


__all__ = ["FaucetRequest", "FaucetResponse"]
