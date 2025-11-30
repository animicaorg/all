from __future__ import annotations

"""
Faucet models

- FaucetRequest: ask the service to send a small amount of funds to `to`.
  This endpoint is typically **guarded** by an API key and server-side
  rate limits (see security/rate_limit.py and security/auth.py).

- FaucetResponse: returns the broadcast transaction hash and the granted
  amount. Implementations MAY also include a best-effort new balance and/or
  a minimal receipt object depending on node RPC behavior.

All numeric amounts are base units of the chain's native token.
"""

from typing import Any, Dict, Optional

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, PositiveInt, conint
except Exception:  # pragma: no cover - v1 fallback
    from pydantic.v1 import BaseModel, Field, PositiveInt  # type: ignore
    from pydantic.v1.types import conint  # type: ignore

from .common import Address, ChainId, Hash


class FaucetRequest(BaseModel):
    """
    Request test funds from the faucet.

    Fields
    ------
    chain_id: ChainId
        Target chain id (must match server configuration).
    to: Address
        Recipient address to receive funds.
    amount: Optional[int]
        Requested amount in base units; server enforces ceilings and may clamp.
        If omitted, server chooses a default drip size.
    """

    chain_id: ChainId = Field(..., description="Target chain id.")
    to: Address = Field(..., description="Recipient address.")
    amount: Optional[conint(ge=1)] = Field(  # type: ignore[misc]
        default=None,
        description="Requested amount (base units). Server may clamp to its maximum.",
    )

    class Config:  # type: ignore[override]
        extra = "forbid"
        anystr_strip_whitespace = True


class FaucetResponse(BaseModel):
    """
    Result of a faucet drip.
    """

    tx_hash: Hash = Field(..., description="Transaction hash of the faucet transfer.")
    granted: PositiveInt = Field(
        ..., description="Granted amount (base units) actually sent."
    )
    new_balance: Optional[conint(ge=0)] = Field(  # type: ignore[misc]
        default=None,
        description="Best-effort new balance of the recipient after inclusion (if known).",
    )
    receipt: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional raw receipt as returned by the node RPC."
    )

    class Config:  # type: ignore[override]
        extra = "forbid"


__all__ = ["FaucetRequest", "FaucetResponse"]
