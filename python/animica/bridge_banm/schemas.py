from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .enums import BridgeDirection, BridgeStatus


class CreateOrderRequest(BaseModel):
    direction: BridgeDirection
    connected_evm_address: str
    amount: str = Field(..., description="Human amount string")
    source_address: str | None = None
    destination_address: str | None = None
    source_chain: str | None = None
    destination_chain: str | None = None
    chain_id: int | None = None
    claim_code: str | None = None


class SignatureVerifyRequest(BaseModel):
    signature: str
    signature_type: str = "EIP712"


class DepositAttachRequest(BaseModel):
    tx_hash: str

    @field_validator("tx_hash")
    @classmethod
    def _validate_tx_hash(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate.startswith("0x") or len(candidate) < 10:
            raise ValueError("tx_hash must be a hex hash")
        return candidate


class ClaimCodeConfirmRequest(BaseModel):
    claim_code: str


class OrderResponse(BaseModel):
    order_id: str
    direction: BridgeDirection
    source_chain: str
    destination_chain: str
    source_address: str
    destination_address: str
    signed_evm_address: str | None
    amount_in: int
    amount_out_expected: int
    fee_amount: int
    asset_in: str
    asset_out: str
    deposit_instruction_type: str
    deposit_address: str | None
    deposit_reference: str | None
    deposit_contract_address: str | None
    deposit_function: str | None
    deposit_tx_hash: str | None
    status: BridgeStatus
    confirmation_count_required: int
    confirmation_count_current: int
    settlement_tx_hash: str | None
    release_tx_hash: str | None
    created_at: datetime
    expires_at: datetime
    claim_code_required: bool
    claim_code_confirmed: bool
    claim_code_confirmed_at: datetime | None
    admin_notes: str | None
    manual_review_required: bool
    manual_review_reason: str | None


class CreateOrderResponse(BaseModel):
    order: OrderResponse
    challenge: dict[str, Any] | None
    warnings: list[str]
    ui: dict[str, Any]


class OrderStatusResponse(BaseModel):
    order: OrderResponse
    events: list[dict[str, Any]]


class SolvencyResponse(BaseModel):
    reserve_anm_confirmed: int
    banm_total_supply_wei: int
    pending_forward_mints_wei: int
    pending_reverse_releases_anm: int
    effective_liabilities_wei: int
    available_redeemable_anm: int
    generated_at: datetime


class PauseRequest(BaseModel):
    paused: bool


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
