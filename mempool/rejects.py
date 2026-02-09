from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RejectReason(str, Enum):
    invalid_signature = "invalid_signature"
    invalid_format = "invalid_format"
    chain_id_mismatch = "chain_id_mismatch"
    insufficient_funds = "insufficient_funds"
    nonce_too_low = "nonce_too_low"
    nonce_too_high = "nonce_too_high"
    nonce_gap = "nonce_gap"
    nonce_conflict = "nonce_conflict"
    fee_too_low = "fee_too_low"
    fee_reserved_invalid = "fee_reserved_invalid"
    tx_already_known = "tx_already_known"
    policy_reject = "policy_reject"
    internal_error = "internal_error"


REJECT_CODE: dict[RejectReason, int] = {
    RejectReason.invalid_signature: 2001,
    RejectReason.invalid_format: 2002,
    RejectReason.chain_id_mismatch: 2003,
    RejectReason.insufficient_funds: 2004,
    RejectReason.nonce_too_low: 2005,
    RejectReason.nonce_too_high: 2006,
    RejectReason.nonce_gap: 2007,
    RejectReason.nonce_conflict: 2008,
    RejectReason.fee_too_low: 2009,
    RejectReason.fee_reserved_invalid: 2010,
    RejectReason.tx_already_known: 2011,
    RejectReason.policy_reject: 2012,
    RejectReason.internal_error: 2999,
}


@dataclass(frozen=True)
class MempoolReject:
    reason: RejectReason
    code: int
    message: str
    hint: str
    context: dict[str, Any] = field(default_factory=dict)
    error_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reason": self.reason.value,
            "reason_code": self.reason.value,
            "code": int(self.code),
            "message": self.message,
            "hint": self.hint,
            "context": dict(self.context),
        }
        if self.error_class:
            payload["error_class"] = self.error_class
            payload["context"].setdefault("error_class", self.error_class)
        return payload


def reject(reason: RejectReason, *, message: str, hint: str, context: dict[str, Any] | None = None, error_class: str | None = None) -> MempoolReject:
    return MempoolReject(
        reason=reason,
        code=REJECT_CODE[reason],
        message=message,
        hint=hint,
        context=context or {},
        error_class=error_class,
    )
