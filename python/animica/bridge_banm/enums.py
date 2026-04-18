from __future__ import annotations

from enum import Enum


class BridgeDirection(str, Enum):
    ANM_TO_BANM = "ANM_TO_BANM"
    BANM_TO_ANM = "BANM_TO_ANM"


class BridgeStatus(str, Enum):
    CREATED = "CREATED"
    AWAITING_DEPOSIT = "AWAITING_DEPOSIT"
    DEPOSIT_SEEN = "DEPOSIT_SEEN"
    CONFIRMING = "CONFIRMING"
    CONFIRMED = "CONFIRMED"
    READY_TO_SETTLE = "READY_TO_SETTLE"
    SETTLEMENT_SUBMITTED = "SETTLEMENT_SUBMITTED"
    SETTLEMENT_CONFIRMED = "SETTLEMENT_CONFIRMED"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    CANCELLED = "CANCELLED"


class BridgeAsset(str, Enum):
    ANM = "ANM"
    BANM = "BANM"


class ChainKind(str, Enum):
    ANIMICA = "ANIMICA"
    BNB = "BNB"
    EVM = "EVM"

