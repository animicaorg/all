"""L2 fee market — integer nanos only, tuned for viable micropayments.

The fee a tx must pay is the marginal resource it imposes:

    fee = base                          (anti-spam floor, admission + a state write)
        + da_per_byte   * encoded_bytes (data-availability: bytes anchored to L1)
        + exec_per_unit * exec_units    (execution work; BATCH_PAYMENT scales w/ N)
        + priority                      (optional tip the sender adds via tx.fee)

Defaults make a 0.01 ANM AI micropayment economically sensible: a ~180-byte
INFERENCE_PAYMENT costs on the order of a few hundred nanos (~1e-7 ANM), i.e.
the fee is a rounding error against the payment. BATCH_PAYMENT is charged per
recipient but far below N individual transfers, which is the whole point of the
primitive.

The signed ``tx.fee`` is the sender-authorized ceiling (max fee). Execution
charges the schedule fee and reverts if the ceiling is below it, so a wallet can
authorize a stable max without knowing the exact byte count in advance. Any
excess (ceiling − charged) is NOT taken — only the marginal fee is collected.

Everything here is deterministic and consensus-relevant (it moves balances), so
the schedule constants are part of the protocol; the sequencer may not deviate.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import TxType


@dataclass(frozen=True)
class FeeSchedule:
    base: int = 100  # 1e-7 ANM
    da_per_byte: int = 2
    exec_per_unit: int = 20
    # Execution-unit weights per tx type (relative cost of the state transition).
    transfer_units: int = 1
    payment_units: int = 1  # AGENT / INFERENCE
    withdraw_units: int = 2  # touches bridge accounting
    escrow_units: int = 2
    batch_unit_per_recipient: int = 1

    def _encoded_len(self, t) -> int:
        try:
            return len(t.body_bytes())
        except Exception:
            return 256  # conservative fallback; never trust an un-encodable tx cheaply

    def exec_units(self, t) -> int:
        tt = t.tx_type
        if tt in (TxType.TRANSFER, TxType.PAY):
            return self.transfer_units
        if tt in (TxType.AGENT_PAYMENT, TxType.INFERENCE_PAYMENT):
            return self.payment_units
        if tt in (TxType.WITHDRAW, TxType.FORCED_WITHDRAW):
            return self.withdraw_units
        if tt in (TxType.ESCROW_OPEN, TxType.ESCROW_RELEASE, TxType.ESCROW_REFUND):
            return self.escrow_units
        if tt == TxType.BATCH_PAYMENT:
            n = len(t.payload.payments) if hasattr(t.payload, "payments") else 1
            return max(1, n) * self.batch_unit_per_recipient
        if tt == TxType.DEPOSIT_CLAIM:
            return 0  # protocol-minted; no user fee
        return self.transfer_units

    def fee_for(self, t) -> int:
        """Required fee in nanos. Protocol-minted DEPOSIT_CLAIM is free."""
        if t.tx_type == TxType.DEPOSIT_CLAIM:
            return 0
        return (
            self.base
            + self.da_per_byte * self._encoded_len(t)
            + self.exec_per_unit * self.exec_units(t)
        )

    def estimate(self, t) -> dict:
        """Human/RPC-facing breakdown for l2_estimateFee."""
        if t.tx_type == TxType.DEPOSIT_CLAIM:
            return {"base": 0, "da": 0, "exec": 0, "total": 0}
        da = self.da_per_byte * self._encoded_len(t)
        ex = self.exec_per_unit * self.exec_units(t)
        return {"base": self.base, "da": da, "exec": ex, "total": self.base + da + ex}


DEFAULT_FEES = FeeSchedule()
